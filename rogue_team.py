"""虚构推演事务入口、推送与通用战斗适配。"""

import time

from battle_rewards import grant_rewards, spend_items
from currency_data import build_currency_balances
from gameplay_protocol import lua_value, read_client_data
from rogue_team_commands import COMMANDS
from rogue_team_events import EVENTS
from rogue_team_options import OPTIONS
from rogue_team_rules import RULES

ROGUE_COMMANDS = {88004, 88008, 88024, 88030, 88100, 88102, 88200, 88202, 88208, 88212,
                  88218, 88222, 88224, 88300, 88306, 88312, 88316}


def _execute(user: dict, command, request: dict) -> dict:
    """在独立 Lua 表中执行共用规则，全部校验成功后才更新调用方副本。"""
    state = user.get('rogue_team', {})
    balances = build_currency_balances(user)
    template = read_client_data("(function() local c=require('RogueTeamCfg');return c["
                                + str(int(state['template_id'])) + "] end)()" if state.get('template_id') else
                                "(function()local c=require('RogueTeamCfg');return c[c.all[1]] end)()")
    tech = template['tech_item']
    context = {'now': int(time.time()), 'uid': user['uid'],
               'owned': [h['id'] for h in user.get('heroes', [])],
               'point': balances.get(template['point_item'], 0),
               'claimed': user.get('resident', {}).get('point_rewards', []),
               'tech': balances.get(tech, user.get('materials', {}).get(str(tech), 0))}
    result = read_client_data(f'''(function()
        local function execute(state,context,command,request)
            {RULES}\n{EVENTS}\n{OPTIONS}\n{COMMANDS}
        end
        local ok,value=pcall(execute,{lua_value(state)},{lua_value(context)},
                            {lua_value(command)},{lua_value(request)})
        if not ok then return {{error=tostring(value)}} end
        return value
    end)()''')
    if 'error' in result:
        raise ValueError(result['error'])
    if result['costs']:
        spend_items(user, result['costs'])
    if result['rewards']:
        grant_rewards(user, result['rewards'])
    user['rogue_team'] = result['state']
    return result


def rogue_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """返回业务响应和有序推送，由 TCP 层统一编码后保存。"""
    result = _execute(user, command, request)
    return result['reply'], [tuple(row) for row in result['pushes']]


def rogue_pushes(user: dict, *, battle_result: bool = False) -> list[tuple]:
    """恢复地图及待处理选择；战斗结果通知仅用于战斗结算返回。"""
    result = _execute(user, 'pushes', {'battle_result': battle_result})
    return [tuple(row) for row in result['pushes']]


def rogue_battle_pushes(user: dict) -> list[tuple]:
    """开战前恢复已校验的节点和地图，避免客户端以空路径计算战斗词缀。"""
    result = _execute(user, 'battle_pushes', {})
    return [tuple(row) for row in result['pushes']]


def handles_rogue_claim(request: dict) -> bool:
    """识别本玩法的通用积分领奖请求，混合请求交由规则整体验证。"""
    return read_client_data(f'''(function()
        for _,id in ipairs({lua_value(request.get('point_reward_id_list', []))}) do
            local c=require('ActivityPointRewardCfg')[id]
            if c and require('ActivityCfg')[c.activity_id].activity_template==82 then return true end
        end
        return false
    end)()''')


def prepare_rogue_battle(user: dict, common: dict) -> int:
    """将客户端节点编号映射为关卡，校验路线、队伍和剩余生命。"""
    return _execute(user, 'prepare', common)['stage_id']


def validate_rogue_battle(user: dict, battle: dict) -> None:
    """拒绝旧局战斗；已结算的重试仍由原战斗缓存返回。"""
    _execute(user, 'validate', {'serial': battle.get('rogue_serial'),
                              'node_id': battle['common']['dest'],
                              'cached': battle.get('settlement') is not None})


def complete_rogue_battle(user: dict, battle: dict, seconds: int) -> None:
    """保存真实战斗结果与生命值，胜利后开启一次局内奖励选择。"""
    _execute(user, 'complete', {'serial': battle['rogue_serial'],
                              'node_id': battle['common']['dest'],
                              'seconds': seconds, 'result': battle['reported']['result'],
                              'info': battle['reported']['info']})
