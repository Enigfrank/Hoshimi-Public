"""按实际客户端清单校验并应用战斗结算 LuaJIT 字节码补丁。"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil

import UnityPy

from config import STREAMING_ASSETS_DIR


CALLBACK = 'BattleCallLuaCallBack'
WAIT_CALLBACK = 'BattleCallLuaCallBackWait'
WAIT_LOADING = 'BattleCallLuaWaitLoading'


def uleb128(data: bytes, offset: int) -> tuple[int, int]:
    """读取 LuaJIT 无符号 LEB128，并返回数值和下一个偏移。"""
    value = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if byte < 0x80:
            return value, offset
        shift += 7


def encode_uleb128(value: int) -> bytes:
    """以 LuaJIT 使用的最短 LEB128 格式编码非负整数。"""
    result = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def emit_uleb128(data: bytearray, value: int) -> None:
    """把非负整数直接追加为 LuaJIT 无符号 LEB128。"""
    data.extend(encode_uleb128(value))


def walk_prototypes(data: bytes, offset: int, stripped: bool, path: tuple = ()):
    """生成每个 LuaJIT 原型的指令区间、常量及子原型位置。"""
    size, cursor = uleb128(data, offset)
    if not size:
        return
    end = cursor + size
    flags = data[cursor]
    arguments, framesize, upvalues = data[cursor + 1:cursor + 4]
    cursor += 4
    constants_count, cursor = uleb128(data, cursor)
    numeric_count, cursor = uleb128(data, cursor)
    instruction_count, cursor = uleb128(data, cursor)
    debug_size = 0
    if not stripped:
        debug_size, cursor = uleb128(data, cursor)
    if debug_size:
        _, cursor = uleb128(data, cursor)
        _, cursor = uleb128(data, cursor)
    instructions_offset = cursor
    cursor += instruction_count * 4
    cursor += upvalues * 2
    constants = []
    children = []
    for _ in range(constants_count):
        kind, cursor = uleb128(data, cursor)
        if kind >= 5:
            length = kind - 5
            constants.append(data[cursor:cursor + length].decode('utf-8', errors='surrogateescape'))
            cursor += length
        elif kind == 0:
            constants.append(None)
            children.append(cursor)
        elif kind == 1:
            constants.append(None)
            array_count, cursor = uleb128(data, cursor)
            hash_count, cursor = uleb128(data, cursor)
            for _ in range(array_count + hash_count * 2):
                item_kind, cursor = uleb128(data, cursor)
                if item_kind >= 5:
                    cursor += item_kind - 5
                elif item_kind in (3, 4):
                    _, cursor = uleb128(data, cursor)
                    if item_kind == 4:
                        _, cursor = uleb128(data, cursor)
        elif kind in (2, 3):
            constants.append(None)
            _, cursor = uleb128(data, cursor)
            _, cursor = uleb128(data, cursor)
        elif kind == 4:
            constants.append(None)
            for _ in range(4):
                _, cursor = uleb128(data, cursor)
        else:
            raise ValueError(f'未知 LuaJIT 常量类型 {kind}')
    for _ in range(numeric_count):
        first = data[cursor]
        _, cursor = uleb128(data, cursor)
        if first & 1:
            _, cursor = uleb128(data, cursor)
    cursor += debug_size
    if cursor != end:
        raise ValueError(f'LuaJIT 原型边界不一致：{path}，起始 {offset}，指令 {instruction_count}，游标 {cursor}，边界 {end}')
    prototype = {
        'path': path, 'offset': offset, 'size': size,
        'data_offset': instructions_offset, 'count': instruction_count,
        'arguments': arguments, 'framesize': framesize, 'constants': constants, 'end': end,
    }
    yield prototype
    # 此客户端按顺序平铺全部原型，子原型常量只用于函数引用。
    if end < len(data):
        yield from walk_prototypes(data, end, stripped, path + (0,))


def patch_bytecode(data: bytes) -> tuple[bytes, dict]:
    """为 goToResult 插入空回调保护，并同步调整原型长度和后续跳转。"""
    if data[:3] != b'\x1bLJ':
        raise ValueError('BattleCallLua 不是 LuaJIT 字节码')
    flags, cursor = uleb128(data, 4)
    stripped = bool(flags & 2)
    if not stripped:
        _, cursor = uleb128(data, cursor)
    target = next((p for p in walk_prototypes(data, cursor, stripped)
                   if p['arguments'] == 4
                   and {CALLBACK, WAIT_CALLBACK, WAIT_LOADING} <= set(p['constants'])), None)
    if target is None:
        raise ValueError('未找到 goToResult 原型')
    words = [int.from_bytes(data[target['data_offset'] + i * 4:target['data_offset'] + i * 4 + 4], 'little')
             for i in range(target['count'])]
    constant_operand = len(target['constants']) - target['constants'].index(CALLBACK) - 1
    instructions = []
    for word in words:
        opcode, a, operand = word & 0xff, (word >> 8) & 0xff, word >> 16
        instructions.append((opcode, a, operand))
    callback_load = 54 | (4 << 8) | (constant_operand << 16)
    callback_call = 66 | (4 << 8) | (1 << 16) | (1 << 24)
    callback_clear = 43 | (4 << 8)
    callback_store = 55 | (4 << 8) | (constant_operand << 16)
    wait_callback_operand = len(target['constants']) - target['constants'].index(WAIT_CALLBACK) - 1
    wait_loading_operand = len(target['constants']) - target['constants'].index(WAIT_LOADING) - 1
    expected_prefix = [
        54 | (4 << 8) | (wait_callback_operand << 16),  # GGET WaitCallback
        14 | (4 << 16),                              # IST D4
        0x80070458,                                   # JMP +7
        54 | (4 << 8) | (wait_loading_operand << 16),  # GGET WaitLoading
        14 | (4 << 16),                              # IST D4
        0x80040458,                                   # JMP +4
        callback_load,                               # GGET Callback
        callback_call,
        callback_clear,
        callback_store,
    ]
    positions = [i for i in range(len(words) - len(expected_prefix) + 1)
                 if words[i:i + len(expected_prefix)] == expected_prefix]
    insertion = callback_load
    guard_jump = 88 | (4 << 8) | ((0x8000 + 2) << 16)
    guarded = [i for i in range(len(words) - 13)
               if words[i:i + 14] == expected_prefix[:2] + [0x800a0458] + expected_prefix[3:5]
               + [0x80070458, insertion, 15 | (4 << 16), guard_jump,
                  callback_load, callback_call, callback_clear, callback_store, 0x0001004b]]
    if len(guarded) == 1:
        return data, {'path': target['path'], 'offset': target['data_offset'] + (guarded[0] + 6) * 4,
                      'original_size': target['size'], 'patched_size': target['size'],
                      'inserted_bytes': 12, 'already_patched': True}
    if len(positions) != 1:
        raise ValueError(f'goToResult 结算指令签名不匹配：原始 {len(positions)} 处，已保护 {len(guarded)} 处')
    position = positions[0] + 6
    patched = bytearray(data)
    size = target['size']
    size_offset = target['offset'] + len(encode_uleb128(size))
    size_bytes = encode_uleb128(size)
    patched_size = encode_uleb128(size + 12)
    shift = len(size_bytes) - len(patched_size)
    patched[target['offset']:size_offset] = patched_size
    offset = target['data_offset'] + shift + position * 4
    inserted = [insertion, 15 | (4 << 16), guard_jump]
    patched[offset:offset] = b''.join(word.to_bytes(4, 'little') for word in inserted)
    instruction_size, instruction_offset = uleb128(data, target['data_offset'] - 1)
    if instruction_size != target['count'] or data[target['data_offset'] - 1] >= 0x80:
        raise ValueError('goToResult 指令计数编码不符合预期')
    count_offset = target['data_offset'] - 1
    patched[count_offset:count_offset + 1] = encode_uleb128(target['count'] + 3)
    # 新指令使所有跨插入点的相对跳转多跨过三条指令。
    for i, word in enumerate(words):
        if word & 0xff != 88:
            continue
        destination = i + 1 + ((word >> 16) - 0x8000)
        if (i < position and destination > position) or (i > position and destination < position):
            new_word = (word & 0xffff) | (((word >> 16) + (3 if i < position else -3)) << 16)
            jump_offset = target['data_offset'] + shift + i * 4
            patched[jump_offset:jump_offset + 4] = new_word.to_bytes(4, 'little')
    verify, detail = patch_bytecode(bytes(patched))
    if verify != bytes(patched) or not detail['already_patched']:
        raise ValueError('LuaJIT 补丁自检失败')
    return bytes(patched), {'path': target['path'], 'offset': offset,
                            'original_size': size, 'patched_size': size + 12,
                            'inserted_bytes': 12, 'already_patched': False}


def script_bytes(asset) -> bytes:
    """按 UnityPy 的 TextAsset 编码规则还原原始字节。"""
    return asset.m_Script.encode('utf-8', errors='surrogateescape')


def prepare_bundle(raw: bytes) -> tuple[bytes, dict, int]:
    """在内存中重打包 scripts64，只修改目标 TextAsset 的结算函数字节码。"""
    prefix_size = raw.find(b'UnityFS\0')
    if prefix_size < 0:
        raise ValueError('scripts64 缺少 UnityFS 标记')
    env = UnityPy.load(raw[prefix_size:])
    targets = [obj for obj in env.objects if obj.type.name == 'TextAsset'
               and obj.read().m_Name.lower() == 'battlecalllua.lua']
    if len(targets) != 1:
        raise ValueError(f'BattleCallLua 数量错误：{len(targets)}')
    asset = targets[0].read()
    patched, detail = patch_bytecode(script_bytes(asset))
    if len(patched) != len(script_bytes(asset)) + 12:
        raise ValueError('字节码补丁长度发生变化')
    asset.m_Script = patched.decode('utf-8', errors='surrogateescape')
    asset.save()
    targets[0].assets_file.mark_changed()
    output = raw[:prefix_size] + next(iter(env.files.values())).save(packer='original')
    return output, detail, targets[0].path_id


def apply_patch() -> None:
    """备份并替换实际脚本包，同时更新清单中的 MD5 与文件大小。"""
    manifest_path = STREAMING_ASSETS_DIR / 'AssetHash_Info.bytes'
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    rows = [row for row in manifest['assetHashList'] if row.startswith('scripts64|')]
    if len(rows) != 1:
        raise ValueError('客户端 scripts64 清单项不唯一')
    _, digest, size = rows[0].split('|')
    bundle_path = STREAMING_ASSETS_DIR / digest[0] / digest[1] / (digest + '.ys')
    raw = bundle_path.read_bytes()
    if len(raw) != int(size) or hashlib.md5(raw).hexdigest() != digest:
        raise ValueError('原始 scripts64 与清单不一致，已停止修改')
    backup = Path(__file__).resolve().parent / 'rescouces' / 'client_patch_backup'
    backup.mkdir(exist_ok=True)
    bundle_backup = backup / (digest + '.ys')
    manifest_backup = backup / 'AssetHash_Info.original.bytes'
    if not bundle_backup.exists():
        shutil.copy2(bundle_path, bundle_backup)
    if not manifest_backup.exists():
        shutil.copy2(manifest_path, manifest_backup)
    if bundle_backup.read_bytes() != raw:
        raise ValueError('已有原始包备份与当前包不同，已停止覆盖')
    patched, detail, path_id = prepare_bundle(raw)
    new_digest = hashlib.md5(patched).hexdigest()
    new_path = STREAMING_ASSETS_DIR / new_digest[0] / new_digest[1] / (new_digest + '.ys')
    new_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = new_path.with_suffix('.tmp')
    temporary.write_bytes(patched)
    os.replace(temporary, new_path)
    new_row = f'scripts64|{new_digest}|{len(patched)}'
    manifest['assetHashList'] = [new_row if row == rows[0] else row for row in manifest['assetHashList']]
    encoded = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    temporary_manifest = manifest_path.with_suffix('.tmp')
    temporary_manifest.write_bytes(encoded)
    os.replace(temporary_manifest, manifest_path)
    print(json.dumps({'old_bundle': str(bundle_path), 'new_bundle': str(new_path),
                      'backup': str(backup), 'path_id': path_id, **detail}, ensure_ascii=False))


def main() -> None:
    """提供显式应用入口，避免导入工具时修改客户端。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    apply_patch()


if __name__ == '__main__':
    main()
