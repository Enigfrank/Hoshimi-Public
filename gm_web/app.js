'use strict';
const byId = id => document.getElementById(id);
let state = { users: [], announcements: [], audit: [] };
let itemPage = 0;
let itemQuery = '';
let dreamOptions = null;

/** 在状态栏显示操作结果。 */
function status(message, result = '') {
  byId('status').textContent = message;
  byId('status').dataset.result = result;
}

/** 请求同源后台，并把错误显示为中文消息。 */
async function api(path, values) {
  const response = await fetch(`/gm/api/${path}`, values === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

/** 获取当前选中的账号。 */
function currentUser() {
  return state.users.find(user => user.uid === Number(byId('account').value));
}

/** 创建文本单元格，所有服务端文本均作为文本显示。 */
function cell(row, value) {
  const td = document.createElement('td');
  td.textContent = String(value ?? '');
  row.append(td);
}

/** 按所选类别展示存档，不提供任意 JSON 覆盖功能。 */
function renderAccount() {
  const user = currentUser();
  if (!user) return;
  const c = user.counts;
  byId('summary').textContent = `${user.online ? '在线' : '离线'} · ${c.heroes} 个角色 · ${c.skins} 款皮肤 · ${c.servants} 种钥从`;
  const field = byId('data-field').value;
  let value = user[field] ?? {};
  if (field === 'currencies') value = user.currency_rows;
  if (field === 'teams') value = Object.fromEntries(Object.entries(user).filter(([key]) => /reserve|team/.test(key)));
  if (field === 'decorations') value = { profile: user.profile, frames: user.unlocked_frames, decorations: user.unlocked_decorations };
  byId('account-data').textContent = JSON.stringify(value, null, 2);
  byId('mail-data').textContent = JSON.stringify(user.mails ?? [], null, 2);
}

/** 重读存档、公告和管理记录，保留当前账号选择。 */
async function refresh() {
  const selected = byId('account').value;
  state = await api('state');
  byId('account').replaceChildren(...state.users.map(user => {
    const option = document.createElement('option');
    option.value = user.uid;
    option.textContent = `${user.uid} · ${user.nick || user.account}`;
    return option;
  }));
  if (state.users.some(user => String(user.uid) === selected)) byId('account').value = selected;
  renderAccount();
  renderNotices();
  await loadDreamOptions();
  await loadPeriods();
  // 三个附加面板各自独立取数，只加载当前可见的一个，避免每次刷新打四个请求。
  if (!byId('progress').hidden) await renderProgress();
  if (!byId('backups').hidden) await renderBackups();
  if (!byId('protocols').hidden) await renderProtocols();
  byId('audit-rows').replaceChildren(...[...state.audit].reverse().map(record => {
    const row = document.createElement('tr');
    const labels = { command: 'GM 指令', mail: '发送邮件', announcement: '保存公告', delete_announcement: '删除公告', unlock: '补齐解锁', backup: '备份存档', restore: '恢复存档', reset_activity: '重置周期活动', activity_periods: '设置全服周期' };
    [new Date(record.time * 1000).toLocaleString('zh-CN'), labels[record.action] || record.action, record.uid, record.details].forEach(v => cell(row, v));
    return row;
  }));
}

/** 读取全服周期设置，显示浏览器本地时区的下次刷新时刻。 */
async function loadPeriods() {
  const { periods } = await api('activity-periods');
  const form = byId('period-form');
  Object.entries(periods).forEach(([key, value]) => { form.elements.namedItem(key).value = value.hours; });
  byId('period-next').textContent = Object.values(periods).map(value =>
    `${value.name}：${new Date(value.next_refresh * 1000).toLocaleString('zh-CN')}`).join('；');
}

/** 读取当前账号梦境周期，并填充周期与 BOSS 选项。 */
async function loadDreamOptions() {
  const user = currentUser();
  if (!user) {
    dreamOptions = null;
    byId('dream-area').replaceChildren();
    byId('dream-boss').replaceChildren();
    return;
  }
  const data = await api(`activity-options?uid=${user.uid}`);
  dreamOptions = data.activities.dream;
  const areaSelect = byId('dream-area');
  const selectedArea = Number(areaSelect.value) || dreamOptions.area_id;
  areaSelect.replaceChildren(...dreamOptions.areas.map(area => {
    const option = document.createElement('option');
    option.value = area.id;
    option.textContent = `周期 ${area.id} · ${area.level_min}-${area.level_max} 级`;
    return option;
  }));
  areaSelect.value = String(selectedArea);
  renderDreamBosses();
}

/** 根据所选周期刷新 BOSS 列表，并标记当前挑战目标。 */
function renderDreamBosses() {
  if (!dreamOptions) return;
  const areaId = Number(byId('dream-area').value);
  const area = dreamOptions.areas.find(row => row.id === areaId);
  if (!area) return;
  const bossSelect = byId('dream-boss');
  const previous = Number(bossSelect.value);
  const fallback = area.id === dreamOptions.area_id ? dreamOptions.boss_group_id : area.boss_list[0]?.id;
  const selected = area.boss_list.some(boss => boss.id === previous) ? previous : fallback;
  bossSelect.replaceChildren(...area.boss_list.map(boss => {
    const option = document.createElement('option');
    option.value = boss.id;
    const current = boss.id === dreamOptions.boss_group_id && area.id === dreamOptions.area_id;
    option.textContent = `BOSS ${boss.id} · ${boss.cleared_stages.length}/${boss.stage_ids.length} 关${current ? '（当前）' : ''}`;
    return option;
  }));
  bossSelect.value = String(selected);
}

/** 执行管理动作，完成后重新读取最新数据。 */
async function action(values) {
  const data = await api('action', { uid: currentUser()?.uid, ...values });
  await refresh();
  status(data.message, 'success');
}

/** 查询当前页物品并显示总数及翻页状态。 */
async function loadItems() {
  const data = await api(`items?q=${encodeURIComponent(itemQuery)}&page=${itemPage}`);
  byId('item-total').textContent = `共 ${data.total} 条匹配记录`;
  byId('page').textContent = `第 ${itemPage + 1} 页`;
  byId('prev').disabled = itemPage === 0;
  byId('next').disabled = (itemPage + 1) * 100 >= data.total;
  byId('item-rows').replaceChildren(...data.items.map(item => {
    const row = document.createElement('tr');
    [item.id, item.name, item.type_name, item.description].forEach(v => cell(row, v));
    return row;
  }));
}

/** 转换时间戳为浏览器当前时区的日期输入值。 */
function localTime(timestamp) {
  const date = new Date(timestamp * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

/** 渲染一组指标卡，用于养成进度与协议覆盖的汇总数字。 */
function renderMetrics(target, entries) {
  byId(target).replaceChildren(...entries.map(([label, value]) => {
    const card = document.createElement('div');
    card.className = 'metric-card';
    const amount = document.createElement('p');
    amount.className = 'metric-value';
    amount.textContent = String(value);
    const caption = document.createElement('p');
    caption.className = 'metric-label';
    caption.textContent = label;
    card.append(amount, caption);
    return card;
  }));
}

/** 读取并展示当前账号的养成进度摘要。 */
async function renderProgress() {
  const user = currentUser();
  if (!user) return;
  const data = (await api(`progress?uid=${user.uid}`)).progress;
  const summary = data.summary ?? {};
  renderMetrics('progress-metrics', [
    ['角色', summary.heroes ?? 0],
    ['钥从', summary.servants ?? 0],
    ['已完成任务', summary.tasks_done ?? 0],
    ['签到天数', summary.sign_days ?? 0]
  ]);
  byId('progress-data').textContent = JSON.stringify(data, null, 2);
}

/** 列出当前账号的存档备份，回滚前需用户二次确认。 */
async function renderBackups() {
  const user = currentUser();
  if (!user) return;
  const data = await api(`backups?uid=${user.uid}`);
  byId('backup-rows').replaceChildren(...data.backups.map(backup => {
    const row = document.createElement('tr');
    [new Date(backup.time * 1000).toLocaleString('zh-CN'), backup.note || '（无备注）',
     backup.nick, backup.level, `${Math.round(backup.size / 1024)} KB`].forEach(v => cell(row, v));
    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'btn-secondary';
    restore.textContent = '恢复';
    restore.addEventListener('click', () => run(async () => {
      const when = new Date(backup.time * 1000).toLocaleString('zh-CN');
      if (confirm(`确认把账号 ${user.uid} 回滚到 ${when} 的存档？回滚前会自动备份当前状态。`)) {
        await action({ action: 'restore', name: backup.name });
      }
    }, restore));
    const operations = document.createElement('td');
    operations.append(restore);
    row.append(operations);
    return row;
  }));
}

/** 展示兜底应答统计，命中项即客户端请求过但没有业务实现的协议。 */
async function renderProtocols() {
  const data = await api('protocols');
  const fallback = data.fallback ?? { total: 0, distinct: 0, commands: [] };
  renderMetrics('protocol-metrics', [
    ['已实现协议', data.implemented ?? 0],
    ['兜底协议数', fallback.distinct ?? 0],
    ['兜底请求次数', fallback.total ?? 0]
  ]);
  byId('protocol-rows').replaceChildren(...fallback.commands.map(command => {
    const row = document.createElement('tr');
    [command.cmd, command.hits,
     command.last_seen ? new Date(command.last_seen * 1000).toLocaleString('zh-CN') : '重启前记录']
      .forEach(v => cell(row, v));
    return row;
  }));
}

/** 将指定公告填入编辑表单。 */
function editNotice(notice) {
  const form = byId('notice-form');
  const values = notice || { id: 0, type: 101, title: '', content: '', start: Math.floor(Date.now() / 1000), end: Math.floor(Date.now() / 1000) + 86400 * 30 };
  for (const key of ['id', 'type', 'title', 'content']) form.elements[key].value = values[key];
  for (const key of ['start', 'end']) form.elements[key].value = localTime(values[key]);
}

/** 展示公告排期并提供编辑与删除。 */
function renderNotices() {
  byId('notice-list').replaceChildren(...state.announcements.map(notice => {
    const row = document.createElement('div'); row.className = 'notice-row';
    const title = document.createElement('h3'); title.textContent = notice.title;
    const time = document.createElement('p'); time.textContent = `${new Date(notice.start * 1000).toLocaleString('zh-CN')} 至 ${new Date(notice.end * 1000).toLocaleString('zh-CN')}`;
    const actions = document.createElement('div'); actions.className = 'actions';
    const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'secondary'; edit.textContent = '编辑'; edit.addEventListener('click', () => editNotice(notice));
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = '删除';
    remove.addEventListener('click', () => run(async () => { if (confirm(`删除公告“${notice.title}”？`)) await action({ action: 'delete_announcement', id: notice.id }); }));
    actions.append(edit, remove); row.append(title, time, actions); return row;
  }));
}

/** 捕获交互错误，并在执行期间禁止重复提交。 */
async function run(task, button) {
  if (button) button.disabled = true;
  try { await task(); } catch (error) { status(error.message, 'error'); }
  finally { if (button) button.disabled = false; }
}

/** 绑定表单提交，交由统一错误处理器执行。 */
function submit(id, handler) {
  byId(id).addEventListener('submit', event => {
    event.preventDefault();
    run(() => handler(new FormData(event.currentTarget)), event.currentTarget.querySelector('button[type="submit"]'));
  });
}

document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-tab]').forEach(tab => {
    if (tab === button) {
      tab.classList.add('active');
      tab.setAttribute('aria-pressed', 'true');
    } else {
      tab.classList.remove('active');
      tab.setAttribute('aria-pressed', 'false');
    }
  });
  document.querySelectorAll('.panel').forEach(panel => { panel.hidden = panel.id !== button.dataset.tab; });
  if (button.dataset.tab === 'items') run(loadItems);
  // 附加面板按需取数，切到可见时才发起请求。
  if (button.dataset.tab === 'progress') run(renderProgress);
  if (button.dataset.tab === 'backups') run(renderBackups);
  if (button.dataset.tab === 'protocols') run(renderProtocols);
}));
byId('account').addEventListener('change', event => run(async () => {
  renderAccount();
  await loadDreamOptions();
  // 进度与备份都按账号维度取数，切换账号必须重新读取。
  if (!byId('progress').hidden) await renderProgress();
  if (!byId('backups').hidden) await renderBackups();
}, event.currentTarget));
byId('data-field').addEventListener('change', renderAccount);
byId('refresh').addEventListener('click', event => run(async () => { await refresh(); status('数据已刷新', 'success'); }, event.currentTarget));
byId('unlock').addEventListener('click', event => run(() => action({ action: 'unlock' }), event.currentTarget));
byId('dream-area').addEventListener('change', renderDreamBosses);
byId('dream-switch').addEventListener('click', event => run(async () => {
  const areaId = Number(byId('dream-area').value);
  const groupId = Number(byId('dream-boss').value);
  if (!areaId || !groupId) throw new Error('请选择梦境周期和 BOSS');
  if (confirm(`确认切换账号 ${currentUser().uid} 的梦境目标为周期 ${areaId} / BOSS ${groupId}？只会清理该 BOSS 的关卡进度。`)) {
    await action({ action: 'reset_activity', activity: 'dream', area_id: areaId, group_id: groupId });
  }
}, event.currentTarget));
document.querySelectorAll('[data-activity-reset]').forEach(button => button.addEventListener('click', () => run(async () => {
  const activity = button.dataset.activityReset;
  if (confirm(`确认重置当前账号的${button.textContent.replace('重置', '')}？该操作会清空对应周期进度。`)) {
    await action({ action: 'reset_activity', activity });
  }
}, button)));
submit('backup-form', form => action({ action: 'backup', note: form.get('note').trim() }));
submit('period-form', async form => {
  const hours = Object.fromEntries(['dream', 'core', 'polyhedron', 'neural'].map(key => [key, Number(form.get(key))]));
  if (Object.values(hours).some(value => !Number.isInteger(value) || value < 1 || value > 8760)) {
    throw new Error('刷新周期须为 1 至 8760 的整数小时');
  }
  if (confirm('确认修改全服周期？更改的玩法从现在重新计时，当前进度不会立即清空。')) {
    await action({ action: 'activity_periods', hours });
  }
});
submit('command-form', form => action({ action: 'command', command: form.get('command') }));
submit('search-form', async () => { itemPage = 0; itemQuery = byId('item-search').value.trim(); await loadItems(); });
byId('prev').addEventListener('click', () => run(async () => { itemPage--; await loadItems(); }));
byId('next').addEventListener('click', () => run(async () => { itemPage++; await loadItems(); }));
submit('mail-form', form => {
  const rewards = form.get('rewards').trim().split(/\r?\n/).filter(Boolean).map(line => {
    const values = line.trim().split(/\s+/);
    if (values.length !== 2 || values.some(v => !/^\d+$/.test(v))) throw new Error('附件格式：每行填写物品ID 数量');
    return { id: Number(values[0]), num: Number(values[1]) };
  });
  return action({ action: 'mail', uid: form.get('scope') === 'all' ? 0 : currentUser().uid, title: form.get('title'), content: form.get('content'), sender: form.get('sender'), days: Number(form.get('days')), rewards });
});
submit('notice-form', form => action({ action: 'announcement', id: Number(form.get('id')), type: Number(form.get('type')), title: form.get('title'), content: form.get('content'), start: Math.floor(new Date(form.get('start')).getTime() / 1000), end: Math.floor(new Date(form.get('end')).getTime() / 1000) }));
byId('new-notice').addEventListener('click', () => editNotice());
editNotice();
run(async () => { await refresh(); status('数据已加载', 'success'); });
