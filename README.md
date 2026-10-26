# Hoshimi Python 服务端

本仓库保存 Python 服务端核心源码，包括 HTTP/CONNECT 接入、游戏 TCP 网关、战斗 UDP、协议编解码、账号与玩法逻辑以及本机 GM 网页。

本项目是源码运行版本，依赖匹配的游戏客户端 DLL 和 Lua 数据；仅下载本仓库不能直接运行。Go 编译版说明由其他仓库维护。

## 目录说明

| 文件或目录 | 用途 |
| --- | --- |
| server.py | 服务入口、连接与协议分发 |
| config.py | 路径、网络、客户端版本及资源配置 |
| proto_bridge.py、native_protocol.py | Lua/DLL 桥与原生协议 |
| auto_responder.py、protocol_map.json | 协议映射及兜底应答 |
| account_*.py、user_storage.py | 账号认证、初始化、持久化 |
| battle_*.py、hero_*.py、resident_*.py 等 | 按领域划分的玩法 |
| hotfix.py、cdn_connection.py | 热更新与资源接入 |
| gm_admin.py、gm_web/ | 本机 GM 接口与页面 |
| rescouces/requirements.txt | 核心依赖说明 |
| rescouces/scripts/gen_cert.ps1 | 旧 HTTPS 模式证书生成 |
| LICENSE、THIRD_PARTY_NOTICES.txt | 许可与第三方声明 |

rescouces 保留原项目拼写。根目录公共 Python 模块一起同步，避免遗漏间接导入。

不包含：真实账号、聊天、GM 备份、本机配置、证书私钥、游戏安装包、DLL、extracted_lua、逆向工具及测试资源。protocol_map.json 是服务端所需协议映射，随源码提供。

## 环境要求

- Windows，PowerShell 7。
- Python 3.10+，必须为 x86_64 的 64 位解释器。
- 匹配版本的 Windows 游戏客户端、tolua.dll 和 Lua 导出文件。
- 核心仅使用 Python 标准库，无须 pip 安装第三方包。资源提取工具的依赖不属于核心依赖。

~~~powershell
python --version
python -c "import struct; print(struct.calcsize('P') * 8)"
~~~

第二条命令应输出 64。

## 准备客户端资源

1. 准备与协议及 Lua 数据匹配的 Windows 游戏客户端。使用 Hoshimi 编译版完成资源准备后，Lua 数据位于其运行目录下的 runtime/lua（默认在 Hoshimi.exe 旁；自定义运行目录时以实际路径为准）。
2. 确认存在 AetherGazer_Data\Plugins\x86_64\tolua.dll。
3. 在 config_local.py 中将 LUA_SRC_DIR 指向上述 runtime/lua，或把完整 Lua 文件复制到本仓库根目录 extracted_lua。Python 不会自动发现 Hoshimi 的运行目录；协议桥需要 p10_pb 等协议模块及相关依赖，须确认该目录包含完整且版本匹配的文件。
4. 确认 AetherGazer_Data\StreamingAssets\Windows 中的客户端资源可用。

仓库没有附带游戏资源、Lua 提取工具或一键资源准备脚本。已有开发环境可直接配置现有 Lua 目录。不同版本的 DLL、Lua 和协议混用可能导致启动、编码或玩法错误。

## 配置

客户端路径可通过环境变量设置：

~~~powershell
Set-Location E:\Project\Hoshimi-public-py
$env:AGL_CLIENT_DIR = 'D:\Games\AetherGazer'
$env:AGL_HOST = '127.0.0.1'
~~~

需要指定 Lua 路径时，在仓库根目录创建 config_local.py：

~~~python
from pathlib import Path

GAME_CLIENT_DIR = Path(r"D:\Games\AetherGazer")
STREAMING_ASSETS_DIR = GAME_CLIENT_DIR / "AetherGazer_Data" / "StreamingAssets" / "Windows"
TOLUA_PATH = GAME_CLIENT_DIR / "AetherGazer_Data" / "Plugins" / "x86_64" / "tolua.dll"
LUA_SRC_DIR = Path(r"D:\Hoshimi\runtime\lua")
HOST = "127.0.0.1"
~~~

示例假设 Hoshimi 编译版运行目录为 D:\Hoshimi，请替换为实际路径；也可以指定自行准备的完整 Lua 目录。本地配置在默认值和环境变量之后加载，同名配置以本地配置为准。覆盖 GAME_CLIENT_DIR 时，要同时设置依赖它的两个路径，如示例所示。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| AGL_CLIENT_DIR | D:\Games\AetherGazer | 客户端根目录 |
| AGL_STREAMING_ASSETS_DIR | 由客户端路径推导 | Windows 资源目录 |
| AGL_TOLUA_PATH | 由客户端路径推导 | x64 tolua.dll |
| AGL_HOST | 0.0.0.0 | 监听地址，本机使用可改 127.0.0.1 |
| AGL_HTTP_PORT | 8080 | HTTP/CONNECT |
| AGL_TCP_PORT | 5001 | 游戏 TCP 与战斗 UDP |
| AGL_ENABLE_HTTPS | 0 | 启用旧 HTTPS 模式 |
| AGL_HTTPS_PORT | 443 | 旧 HTTPS 端口 |
| AGL_CLIENT_VERSION | 307 | 客户端版本参数 |
| AGL_CLIENT_VERSION_NAME | v5.2.0 | 客户端版本名 |
| AGL_RESOURCE_MODE | proxy | proxy 本地转发；direct 官方 CDN |
| AGL_HOTFIX_URL | 本机 HTTP 端口下 /hotfix/ | 热更新地址 |
| AGL_RESOURCE_DOWNLOAD_URL | config.py 中的官方资源地址 | 资源下载上游 |

版本值是现有源码默认配置，不代表兼容所有版本。LUA_SRC_DIR 通过本地配置覆盖，当前没有专用环境变量。

## 启动与访问

~~~powershell
Set-Location E:\Project\Hoshimi-public-py
python -B -c "from config import validate_environment; ok, issues = validate_environment(); print('\n'.join(issues) or '基础环境检查通过'); raise SystemExit(0 if ok else 1)"
python -B server.py
~~~

基础环境检查不代替完整启动验证。启动继续执行协议自检，成功后监听 HTTP、TCP、UDP。默认分别使用 8080、5001、5001；启用旧 HTTPS 时才使用 443。按 Ctrl+C 停止。

本机 GM 页面地址为 http://127.0.0.1:8080/gm/，HTTP 端口变化时相应调整。GM 校验回环来源、本机 Host 与同源请求。

客户端需要匹配的接入配置或配套脚本。本仓库没有附带客户端切换脚本，启动服务不会自动完成客户端接入配置。

## 可选旧 HTTPS 模式

默认不需要证书。使用旧模式时：

~~~powershell
pwsh -File .\rescouces\scripts\gen_cert.ps1
$env:AGL_ENABLE_HTTPS = '1'
python -B server.py
~~~

证书和私钥生成在仓库根目录，已被 Git 忽略。信任证书与客户端重定向须按实际接入方案配置，生成证书本身不完成接入。

## 存档与备份

账号、聊天、GM 操作及备份等数据位于 data/，整个目录被 Git 忽略。协议覆盖报告位于 rescouces/protocol_coverage.md；运行时客户端补丁备份位于 rescouces/client_patch_backup/，也不提交。

停服后备份整个 data/；恢复前先备份现状。本仓库未复制原工作目录真实账号，不会自动继承旧存档。本机配置与证书需要单独保管。


## 故障排查

| 现象 | 排查 |
| --- | --- |
| DLL 不存在 | 检查 TOLUA_PATH、本地配置与客户端路径 |
| DLL 加载失败 | 检查解释器 x64、DLL 版本及系统依赖 |
| Lua 目录不存在或 require 失败 | 检查 LUA_SRC_DIR、协议模块与依赖完整性 |
| 协议自检失败 | 核对 DLL、Lua、协议映射和客户端版本，保留具体异常 |
| 端口绑定失败 | 检查 8080/5001 TCP 和 5001 UDP 占用，修改后同步客户端配置 |
| GM 无法访问 | 使用本机回环地址和实际 HTTP 端口，确认服务启动成功 |
| 客户端无法连接 | 检查接入配置、监听地址及 HTTP/TCP/UDP 端口 |
| 资源加载失败 | 检查资源完整性、版本、上游连接与 proxy/direct 配置 |

## 许可

使用和再分发前阅读 LICENSE 与 THIRD_PARTY_NOTICES.txt。游戏资源与 DLL 由使用者自行准备。