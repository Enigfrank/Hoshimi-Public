"""使用当前客户端 protobuf 描述编码玩法数据，避免旧协议表漏字段。"""

import json

from proto_bridge import get_bridge


def lua_value(value) -> str:
    """把协议数据转换为 Lua 字面量，字符串按 UTF-8 字节转义。"""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return '"' + ''.join(f"\\{byte:03d}" for byte in value.encode('utf-8')) + '"'
    if isinstance(value, (list, tuple)):
        return '{' + ','.join(lua_value(item) for item in value) + '}'
    if isinstance(value, dict):
        return '{' + ','.join(f'[{lua_value(key)}]={lua_value(item)}' for key, item in value.items()) + '}'
    raise TypeError(f"不支持的协议数据类型：{type(value).__name__}")


LUA_JSON = r'''
local function json_value(value)
    local kind = type(value)
    if kind == 'nil' then return 'null' end
    if kind == 'number' or kind == 'boolean' then return tostring(value) end
    if kind == 'string' then
        return '"' .. value:gsub('[%z\1-\31\\"]', function(c)
            return string.format('\\u%04x', string.byte(c))
        end) .. '"'
    end
    local parts = {}
    if #value > 0 or (getmetatable(value) or {}).json_array then
        for _, child in ipairs(value) do parts[#parts+1] = json_value(child) end
        return '[' .. table.concat(parts, ',') .. ']'
    end
    -- 配置表按键名排序输出：业务按目录次序生成协议数组，
    -- Python 的 dict 会保留此顺序，Go 的 map 不会，排序后两侧才能一致复现。
    local keys = {}
    for key in pairs(value) do keys[#keys+1] = key end
    table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
    for _, key in ipairs(keys) do
        parts[#parts+1] = json_value(tostring(key)) .. ':' .. json_value(value[key])
    end
    return '{' .. table.concat(parts, ',') .. '}'
end
'''


def read_client_data(expression: str):
    """读取指定客户端配置表达式，只用于项目内固定查询。"""
    raw = get_bridge().run_lua_expr(f"(function() {LUA_JSON}\nreturn json_value({expression}) end)()")
    return json.loads(raw)


def encode_message(command: int, values: dict, direction: str = 'sc') -> bytes:
    """按真实描述递归赋值并编码，测试请求同样使用客户端 CS 描述。"""
    if direction not in ('cs', 'sc'):
        raise ValueError('协议方向必须为 cs 或 sc')
    return get_bridge().run_lua_expr(f'''(function()
        local function fill(message, data)
            local descriptor = getmetatable(message)._descriptor
            local known = {{}}
            for _, field in ipairs(descriptor.fields) do
                local name, value = field.name, data[field.name]
                known[name] = true
                if field.label == 3 then
                    for _, child in ipairs(value or {{}}) do
                        if field.type == 11 then fill(message[name]:add(), child)
                        else message[name]:append(child) end
                    end
                elseif field.type == 11 then
                    if value ~= nil or field.label == 2 then
                        fill(message[name], value or {{}})
                        message[name]:SetInParent()
                    end
                elseif value ~= nil then message[name] = value
                elseif field.label == 2 then
                    if field.type == 8 then message[name] = false
                    elseif field.type == 9 or field.type == 12 then message[name] = ''
                    else message[name] = field.default_value or 0 end
                end
            end
            for key in pairs(data) do assert(known[key], '未知字段: ' .. key) end
        end
        local message = require('p{command // 1000}_pb').{direction}_{command}()
        fill(message, {lua_value(values)})
        return message:SerializeToString()
    end)()''')


def decode_message(command: int, payload: bytes, direction: str = 'cs') -> dict:
    """读取实际协议字段，保留空数组并跳过未设置的嵌套消息。"""
    if direction not in ('cs', 'sc'):
        raise ValueError('协议方向必须为 cs 或 sc')
    return read_client_data(f'''(function()
        local function plain(message)
            local result = {{}}
            for _, field in ipairs(getmetatable(message)._descriptor.fields) do
                local value = message[field.name]
                if field.label == 3 then
                    local children = setmetatable({{}}, {{json_array=true}})
                    for _, child in ipairs(value) do
                        if field.type == 11 then children[#children+1] = plain(child)
                        elseif field.type == 3 or field.type == 4 or field.type == 6 or field.type == 16 or field.type == 18 then
                            children[#children+1] = tonumber(child)
                        else children[#children+1] = child end
                    end
                    result[field.name] = children
                elseif field.type == 11 then
                    if message:HasField(field.name) then result[field.name] = plain(value) end
                elseif field.type == 3 or field.type == 4 or field.type == 6 or field.type == 16 or field.type == 18 then
                    result[field.name] = tonumber(value)
                else result[field.name] = value end
            end
            return result
        end
        local message = require('p{command // 1000}_pb').{direction}_{command}()
        local raw = ('{payload.hex()}'):gsub('..', function(c) return string.char(tonumber(c,16)) end)
        message:ParseFromString(raw)
        return plain(message)
    end)()''')


def message_has_field(command: int, payload: bytes, name: str, direction: str = 'cs') -> bool:
    """判断可选字段在原始报文里是否真的出现。

    ``decode_message`` 会把未设置的 optional 标量解成类型零值，例如
    ``cs_14514.picture_url`` 未携带时得到 ``''``。誓约用同一个指令区分
    "建立誓约"和"上传婚纱照"，只能回到原始 protobuf 判断字段是否存在。
    """
    if direction not in ('cs', 'sc'):
        raise ValueError('协议方向必须为 cs 或 sc')
    return bool(read_client_data(f'''(function()
        local message = require('p{command // 1000}_pb').{direction}_{command}()
        local raw = ('{payload.hex()}'):gsub('..', function(c) return string.char(tonumber(c,16)) end)
        message:ParseFromString(raw)
        return message:HasField('{name}')
    end)()'''))
