"""浮光绎曲的同局成绩、完成标记、评价任务与恢复协议。"""

from functools import lru_cache

from gameplay_protocol import read_client_data
from resident_catalog import resident_config, resident_rows
from resident_data import resident_state


@lru_cache(maxsize=1)
def music_grades() -> list:
    """读取客户端S+/S/A/B/C/F评价阈值，不把完成标记当作准确率等级。"""
    return read_client_data("require('GameSetting').attach_music_grade.value")


def music_data(music: dict) -> dict:
    """只下发61047定义的字段，排除原生节奏玩法的内部耗时。"""
    return {'challenge_info': [{key: row[key] for key in ('id', 'score', 'sign')}
                               for row in music['challenge_info']]}


def complete_music(user: dict, request: dict) -> dict:
    """完成局的最高分与标记一起保存；未完成局不覆盖已完成成绩。"""
    cfg = resident_config('ActivityMusicCfg', request['id'])
    score, sign = request['score'], request['state']
    if not 0 <= score <= cfg['total_score'] or sign not in (0, 1, 2, 3):
        raise ValueError('音游成绩无效')
    rows = resident_state(user)['music']['challenge_info']
    row = next((r for r in rows if r['id'] == cfg['id']), None)
    if row is None:
        rows.append({'id': cfg['id'], 'score': score, 'sign': sign})
    elif sign > 0 and (row['sign'] == 0 or score >= row['score']) or sign == row['sign'] == 0 and score >= row['score']:
        row.update(score=score, sign=sign)
    return {'result': 0}


def music_grade_task(user: dict, params: list) -> int:
    """评价任务要求对应曲目、难度、完整通关和配置的最低准确率同时满足。"""
    activity, difficulty, grade = params
    grades = music_grades()
    if not 1 <= grade <= len(grades):
        return 0
    songs = {r['id']: r for r in resident_rows('ActivityMusicCfg')
             if r['activity_id'] == activity and r['difficult'] == difficulty}
    return int(any(row['sign'] > 0 and row['id'] in songs and
                   row['score'] * 100 >= songs[row['id']]['total_score'] * grades[grade-1]
                   for row in resident_state(user)['music']['challenge_info']))
