"""未实现协议的运行期追踪，以及业务实现／兜底应答的覆盖报告。

``server.handle_tcp_message`` 对没有业务分支的 CS 指令统一走
``auto_responder.generate_response`` 或通用编码路径，这两条路径按协议元数据
补齐必填字段，返回体数据全为默认值（多数协议 ``result=1``，仅 14042、32054
等少数协议带成功结果）。这类响应不会报错，客户端表现为"点得动、看得开、
数据永远空"，因此必须显式记录，否则无法回答"客户端到底请求了哪些
本服未实现的协议"。

本模块只做观测，不改变任何应答内容。
"""

from collections import Counter
from pathlib import Path
import ast
import json
import re
import threading
import time

import user_storage

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MAP_PATH = BASE_DIR / 'protocol_map.json'
# 报告输出到 rescouces 目录，与既有状态文档同级，便于随源码一起审阅。
DEFAULT_REPORT_PATH = BASE_DIR / 'rescouces' / 'protocol_coverage.md'

_lock = threading.Lock()
# 指令号 -> 命中次数，用于统计客户端真实请求到的未实现协议。
_fallback_hits: Counter = Counter()
# 指令号 -> 最近一次命中的 Unix 时间。
_fallback_seen: dict = {}
# 已由业务分支显式实现的 CS 指令。
_implemented: set = set()

# 模块级常量求值沙箱：只放行构造集合/字典/元组所需的内建函数。
_SAFE_EVAL_GLOBALS = {'__builtins__': {}, 'set': set, 'frozenset': frozenset,
                      'range': range, 'tuple': tuple, 'list': list, 'dict': dict,
                      'int': int, 'str': str, 'float': float, 'bool': bool}
# 只采信名称表明其为协议清单的模块级常量，避免把货币 ID、道具类型、
# 英雄槽位等业务编号表误判为协议号。
_COMMAND_CONSTANT_HINT = ('COMMAND', 'CMD')
# 协议号统一为五位；静态表里的货币、道具、英雄槽位等编号均为三位或更短，
# 因此按位数即可把误纳的业务编号剔除，无需逐表维护白名单。
_PROTOCOL_MIN = 10000
# 常量求值失败的哨兵值，避免与空集合等合法结果混淆。
_EVAL_FAILED = object()


def log_path() -> Path:
    """返回兜底日志的当前路径。

    在调用时读取 ``user_storage.DATA_DIR``，而不是模块导入时固化，
    这样测试与部署切换数据目录后无需重新导入本模块即可生效。
    """
    return user_storage.DATA_DIR / 'gm' / 'protocol_fallback.jsonl'


def note_fallback(command: int) -> None:
    """记录一次由兜底应答处理的 CS 指令及其时间，并追加一条 JSONL 日志。"""
    with _lock:
        _fallback_hits[command] += 1
        _fallback_seen[command] = int(time.time())
        stamp = _fallback_seen[command]
    _append_log(command, stamp)


def _append_log(command: int, stamp: int) -> None:
    """把单次兜底命中追加到 JSONL 日志；写失败只告警，不影响应答线程。"""
    try:
        target = log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps({'time': stamp, 'cmd': command}, ensure_ascii=False) + '\n')
    except OSError as error:
        print(f'[协议覆盖] 兜底日志写入失败（不影响应答）：{error}')


def _load_log() -> Counter:
    """读取历史 JSONL 日志并按指令号累计次数，供跨进程统计使用。"""
    counts: Counter = Counter()
    target = log_path()
    if not target.is_file():
        return counts
    try:
        text = target.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return counts
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # 单行损坏不应让整份统计失效，跳过即可。
            continue
        if isinstance(row, dict) and isinstance(row.get('cmd'), int):
            counts[row['cmd']] += 1
    return counts


def fallback_summary() -> dict:
    """返回兜底协议的统计快照，供 GM 后台与报告使用。

    ``note_fallback`` 既记内存也写日志，因此同一指令两侧计数相同；
    进程重启后内存归零、只余日志。这里逐指令取两侧较大值，
    既不重复累加，也能在日志写入失败时退回到内存计数。
    """
    with _lock:
        runtime = {cmd: [hits, _fallback_seen.get(cmd, 0)] for cmd, hits in _fallback_hits.items()}
    history = _load_log()
    commands = []
    for cmd in sorted(set(runtime) | set(history)):
        memory_hits, memory_seen = runtime.get(cmd, (0, 0))
        commands.append({'cmd': cmd, 'hits': max(history.get(cmd, 0), memory_hits),
                         'last_seen': memory_seen})
    return {'total': sum(row['hits'] for row in commands),
            'distinct': len(commands),
            'commands': commands}


def reset_fallback() -> None:
    """清空运行期内存统计，供测试隔离使用；磁盘日志不在此清除。"""
    with _lock:
        _fallback_hits.clear()
        _fallback_seen.clear()


def implemented_commands() -> set:
    """返回已登记为业务实现的指令号副本。"""
    with _lock:
        return set(_implemented)


def record_implemented(commands) -> None:
    """登记已在分发链中显式处理的 CS 指令。"""
    with _lock:
        _implemented.update(int(command) for command in commands)


def protocol_index(map_path=DEFAULT_MAP_PATH) -> dict:
    """返回按 CS 协议号字符串索引的定义字典。"""
    with open(map_path, encoding='utf-8') as handle:
        raw = json.load(handle)
    return {str(raw[key]['protocol_id']): raw[key] for key in raw
            if key.startswith('cs_') and raw[key].get('direction') == 'cs'}


def source_referenced_commands(root=BASE_DIR) -> set:
    """扫描服务端与业务模块源码，得出出现过的五位协议编号。

    这是静态取证而非运行期真值：只能说明该编号在源码文字中出现过，
    因此报告中与运行期兜底记录分开表述，不把引用等同于已实现。
    """
    found = set()
    for path in Path(root).glob('*.py'):
        if path.name == 'protocol_coverage.py':
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        found.update(int(value) for value in re.findall(r'\b(\d{5})\b', text))
    return found


def dispatch_commands() -> set:
    """解析分发链与业务模块命令集合，得出已被业务处理的协议号。

    分发链里同时存在 ``elif cmd == 20012:``、``if cmd == 30004:``
    和 ``cmd in (14042, 32054)`` 三种写法，因此三种形式都要匹配；
    另有一批指令交给业务模块（如商店 20012、签到、日常），
    这些模块各自导出 ``*_COMMANDS`` 集合、``set(range(...))`` 区间，
    或以协议号作为字典键的常量表，一并解析，避免手工维护一份会过期的清单。

    模块级常量走 AST 解析而非正则展开区间：`set(range(18010, 18035, 2))`
    """
    text = (Path(BASE_DIR) / 'server.py').read_text(encoding='utf-8')
    found = set()
    for body in re.findall(r'\bcmd\s*==\s*(\d{5})', text):
        found.add(int(body))
    for group in re.findall(r'\bcmd\s+in\s+(\([^)]*\))', text):
        found.update(int(value) for value in re.findall(r'\d{5}', group))
    for path in Path(BASE_DIR).glob('*.py'):
        source = path.read_text(encoding='utf-8', errors='ignore')
        for value in _module_constants(path, source):
            found.update(cmd for cmd in _protocol_numbers(value) if cmd >= _PROTOCOL_MIN)
    return found


def _module_constants(path: Path, source: str):
    """解析模块级常量赋值，产出可求值的值。

    常量之间存在引用（如 ``PROFILE_COMMANDS = set(PROFILE_FIELDS)``），
    因此按源码顺序单遍求值：每解析出一个常量就立即注入后续语句的求值环境。
    单遍的前提是引用只向后依赖（后面的常量引用前面已定义的），这与本仓库写法一致。
    求值失败时退回静态数字扫描，只认表达式里的五位数字字面量；
    前向引用（如 ``LATE = EARLY`` 且 ``EARLY`` 在下文定义）取不到数字，
    会被整体跳过，因此新增协议清单时不要写成前向引用。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    scope: dict = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(name.isupper() for name in names):
            continue
        if not any(_is_command_constant(name) for name in names):
            continue
        value = _eval_constant(node.value, path, scope)
        if value is not _EVAL_FAILED:
            for name in names:
                scope.setdefault(name, value)
            yield value


def _eval_constant(node, path: Path, scope: dict):
    """在给定作用域下求值常量表达式，失败返回 ``_EVAL_FAILED`` 标记。"""
    env = dict(_SAFE_EVAL_GLOBALS)
    env.update(scope)
    try:
        return eval(compile(ast.Expression(node), str(path), 'eval'), env)
    except Exception:
        # 引用了不可安全求值的外部符号，退回静态数字扫描。
        numbers = re.findall(r'\b(\d{5})\b', ast.unparse(node))
        return tuple(int(item) for item in numbers) if numbers else _EVAL_FAILED


def _is_command_constant(name: str) -> bool:
    """判断常量名是否代表协议清单。

    ``*_COMMANDS`` / ``*_CMD*`` 是显式协议集合；``PROFILE_FIELDS`` 这类
    以协议号为键的字段映射表由 ``_COMMAND_CONSTANT_PAIR`` 覆盖。
    """
    if any(hint in name for hint in _COMMAND_CONSTANT_HINT):
        return True
    if 'FIELDS' in name or 'MAP' in name:
        return True
    return False

def _protocol_numbers(value) -> set:
    """从常量值中提取协议号：集合取元素、字典取键、嵌套组合递归展开。"""
    numbers: set = set()
    if isinstance(value, (set, frozenset, tuple, list)):
        for item in value:
            if isinstance(item, int) and not isinstance(item, bool):
                numbers.add(item)
            elif isinstance(item, (set, frozenset, tuple, list, dict)):
                numbers |= _protocol_numbers(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, int) and not isinstance(key, bool):
                numbers.add(key)
            if isinstance(item, (set, frozenset, tuple, list, dict)):
                numbers |= _protocol_numbers(item)
    return numbers


def build_report(map_path=DEFAULT_MAP_PATH) -> str:
    """生成 Markdown 覆盖报告，区分业务实现、运行期兜底与静态引用。"""
    index = protocol_index(map_path)
    index_keys = set(index)
    summary = fallback_summary()
    hits = {str(row['cmd']): row for row in summary['commands']}
    implemented = implemented_commands()
    referenced = source_referenced_commands()
    # 兜底命中表按协议号字符串索引；只有真实 CS 协议才写入报告，
    # 其余命中（例如无 SC 映射时的通用响应）只计入 total。
    hits = {cmd: row for cmd, row in hits.items() if cmd in index_keys}

    implemented_ids = sorted(int(key) for key in index if int(key) in implemented)
    # 已实现指令即使历史日志中有兜底命中，也不再属于当前缺口。
    fallback_ids = sorted(int(key) for key in index
                          if key in hits and int(key) not in implemented)
    untouched = sorted(int(key) for key in index
                       if int(key) not in implemented and key not in hits)

    lines = ['# 协议覆盖报告', '',
             '本文件由 `protocol_coverage.write_report()` 生成，'
             '用于回答"客户端请求了哪些本服没有业务实现的协议"。', '',
             '生成命令：`python -B -c "import protocol_coverage as p; p.record_implemented(p.dispatch_commands()); p.write_report()"`。',
             '', '三种状态互不重叠：', '',
             '- **业务实现**：`server.handle_tcp_message` 中存在显式 `cmd` 分支。',
             '- **兜底应答**：客户端实际请求过但没有业务分支，由 `auto_responder` '
             '或通用编码路径补齐必填字段后返回，数据全为默认值。',
             '- **静态引用**：编号只在源码文字中出现（含注释与文档），不等于有业务处理。', '',
             '兜底应答多数协议返回 `result=1`，仅 14042、32054 等少数协议返回成功结果；'
             '无论哪种情况，业务数据都是空的。命中次数取自 `data/gm/protocol_fallback.jsonl`，'
             '因此跨进程重启累计；已实现指令的历史命中不计入当前缺口。', '',
             '## 汇总', '', '| 指标 | 数值 |', '|---|---|',
             f'| CS 协议总数 | {len(index)} |',
             f'| 业务实现 | {len(implemented_ids)} |',
             f'| 兜底应答（运行期实测命中） | {len(fallback_ids)} |',
             f'| 兜底应答累计请求次数 | {sum(hits[str(cmd)]["hits"] for cmd in fallback_ids)} |',
             f'| 未被实现也未被请求 | {len(untouched)} |',
             f'| 源码中出现过的协议号 | {len([c for c in referenced if c in {int(k) for k in index}])} |',
             '', '## 业务实现清单', '', '| CS 协议 | 字段 | 标签 |', '|---|---|---|']
    for cmd in implemented_ids:
        entry = index[str(cmd)]
        lines.append(f'| {cmd} | {_field_text(entry)} | {_label_text(entry)} |')
    if not implemented_ids:
        lines.append('| （未登记） | | |')

    lines += ['', '## 兜底应答明细', '',
              '这些是当前真实缺口：客户端会发起请求，服务端返回空数据。', '',
              '| CS 协议 | 字段 | 标签 | 命中次数 |', '|---|---|---|---|']
    for cmd in fallback_ids:
        entry = index[str(cmd)]
        lines.append(f'| {cmd} | {_field_text(entry)} | {_label_text(entry)} | '
                     f'{hits[str(cmd)]["hits"]} |')
    if not fallback_ids:
        lines.append('| （未记录到兜底应答） | | | |')

    # 按协议前缀（p39_pb 之类）分组：该前缀就是客户端的模块边界，
    # 比 pb 文件名更容易定位到玩法目录。
    by_prefix = {}
    for cmd in untouched:
        entry = index[str(cmd)]
        prefix = str(entry.get('module', '') or '未知')
        by_prefix.setdefault(prefix, []).append(cmd)
    lines += ['', '## 未被请求也未实现的协议前缀分布', '',
              '这些协议尚无运行期证据，不代表客户端不会请求；分布用于排定补全优先级。',
              '', '| 前缀 | 协议数 | 编号 |', '|---|---|---|']
    for prefix in sorted(by_prefix, key=lambda name: -len(by_prefix[name])):
        cmds = by_prefix[prefix]
        lines.append(f'| {prefix} | {len(cmds)} | {"、".join(str(c) for c in cmds)} |')
    return '\n'.join(lines) + '\n'


def _field_text(entry: dict) -> str:
    """把协议字段列表拼成一行可读文本，标注重复与可选字段。"""
    parts = []
    for field in entry.get('fields', []):
        label = field.get('label') or ''
        parts.append(f"{field['name']}（{label}）" if label else str(field['name']))
    return '、'.join(parts) or '（无字段）'


def _label_text(entry: dict) -> str:
    """汇总协议各字段的 required/repeated/optional 标签计数。"""
    counts: Counter = Counter(field.get('label') or '未标注' for field in entry.get('fields', []))
    return '、'.join(f'{label} {counts[label]}' for label in sorted(counts)) or '（无字段）'


def write_report(path=DEFAULT_REPORT_PATH, map_path=DEFAULT_MAP_PATH):
    """把覆盖报告写入磁盘并返回实际路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report(map_path), encoding='utf-8')
    return target
