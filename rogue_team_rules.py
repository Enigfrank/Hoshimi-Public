"""虚构推演共用的服务端状态与地图规则，在现有 Lua 桥中执行。"""

RULES = r'''
-- 保留空数组的 JSON 类型，确保断线恢复后 repeated 字段仍为数组。
local function arr(t) return setmetatable(t or {}, {json_array=true}) end
-- 复制配置与存档，避免修改 Lua 缓存中的客户端配置。
local function copy(t)
    if type(t)~='table' then return t end
    local out={}; for k,v in pairs(t) do out[k]=copy(v) end
    return setmetatable(out,getmetatable(t))
end
-- 读取有效配置行，缺失配置作为业务失败返回。
local function cfg(name,id) return assert(require(name)[id],name..' 配置不存在: '..tostring(id)) end
-- 按客户端顺序枚举配置，维持两种服务端的一致抽取顺序。
local function rows(name)
    local c=require(name);local out={}
    for _,id in ipairs(c.all) do out[#out+1]=c[id] end
    return out
end
-- 判断数组成员，禁止以客户端传入的任意编号访问其他路线。
local function has(t,v) for _,x in ipairs(type(t)=='table' and t or {}) do if x==v then return true end end return false end
-- 在数组中追加唯一记录，供图鉴和历史记录持久化。
local function unique(t,v) if not has(t,v) then t[#t+1]=v end end
local s=state
for _,key in ipairs({'collection_list','his_difficult','his_avg','unlock_collection','view_collection','tree_list','reward_list'}) do s[key]=arr(s[key]) end
s.template_id=s.template_id or rows('RogueTeamCfg')[1].id
s.difficult=s.difficult or 0;s.point=math.max(s.point or 0,context.point or 0);s.last_id=s.last_id or 0
for _,id in ipairs(context.claimed or {}) do unique(s.reward_list,id) end
local template=cfg('RogueTeamCfg',s.template_id)
local out={state=s,pushes=arr(),rewards=arr(),costs=arr(),reply={result=0}}
local r=s.run
if r then
    for _,key in ipairs({'map','second_map','heroes','treasures','items','effects','queue','visits','options'}) do r[key]=arr(r[key]) end
    if r.pending then r.pending.param_list=arr(r.pending.param_list) end
    if r.shop then r.shop.param_list=arr(r.shop.param_list) end
    if r.event then r.event.opt_list=arr(r.event.opt_list) end
    if r.result then
        for _,key in ipairs({'end_info_list','collection_list','unlock_collection'}) do r.result[key]=arr(r.result[key]) end
    end
end
-- 累积有序推送，实际编码和存盘由调用端在事务副本中完成。
local function emit(id,data) out.pushes[#out.pushes+1]=arr({id,data}) end
-- 每局保存随机状态；重连或失败请求不会重新抽奖。
local function random(n)
    assert(n and n>0,'随机候选为空')
    r.rng=(r.rng*48271)%2147483647
    return r.rng%n+1
end
-- 从有限候选中抽取一个，配置为空时拒绝生成无效节点。
local function pick(t) return t[random(#t)] end
-- 按当前模板读取设置，不写死其他客户端版本的数值。
local function setting(id)
    for _,row in ipairs(rows('RogueTeamSetting')) do if row.temp_id==template.setting_temp and row.setting_id==id then return row.value end end
    error('虚构推演设置不存在: '..id)
end
-- 取局内属性，未覆盖的属性使用当前配置默认值。
local function attr(id) return r.attrs[tostring(id)] or cfg('RogueTeamAttributeCfg',id).default end
-- 在配置范围内修改属性，消费不足时拒绝整个请求。
local function change(id,delta)
    local value=attr(id)+delta;assert(value>=0,'局内资源不足')
    local maximum=cfg('RogueTeamAttributeCfg',id).max
    r.attrs[tostring(id)]=maximum>0 and math.min(value,maximum) or value
end
-- 写入单种统计项，统计数据与路线一并存档。
local function stat(id,n) r.stats[tostring(id)]=(r.stats[tostring(id)] or 0)+n end
-- 记录图鉴类型和已获取条目，不重复添加。
local function collect(ty,id)
    local row
    for _,v in ipairs(s.collection_list) do if v.type==ty then row=v end end
    if not row then row={type=ty,item_list=arr()};s.collection_list[#s.collection_list+1]=row end
    unique(row.item_list,id)
    if ty==1 or ty==3 or ty==4 then unique(s.unlock_collection,id) end
end
-- 校验当前局，已结算的路线不能继续消费或推进。
local function active()
    assert(r and not r.settled and s.difficult>0,'虚构推演尚未开局或已经结算')
    return r
end
-- 返回当前主地图或隐藏地图的节点。
local function nodes() return r.hidden and r.second_map or r.map end
-- 查找当前地图节点，拒绝其他层的编号。
local function node(id)
    for _,n in ipairs(nodes()) do if n.node_id==id then return n end end
    error('节点不属于当前地图')
end
-- 建立当前层的可视路线；房间、关卡与列数均取自安装包配置。
local function make_map(floor,hidden)
    local cols={}
    for _,v in ipairs(rows('RogueTeamMapCfg')) do if v.temp_id==template.map_temp and v.tier==floor then cols[#cols+1]=v.col end end
    table.sort(cols);assert(#cols>0,'楼层配置不存在')
    local result=arr();local bycol={}
    for _,col in ipairs(cols) do
        local count=(col==1 or col==cols[#cols]) and 1 or 3
        bycol[col]={}
        for index=1,count do
            local row=count==1 and 3 or index+1
            local ty=1
            if col==cols[#cols] then ty=floor==3 and 4 or 3
            elseif col>1 then
                local weights=setting(1001);local total=0
                for _,w in ipairs(weights) do total=total+w[2] end
                local roll=random(total)
                for _,w in ipairs(weights) do roll=roll-w[2];if roll<=0 then ty=w[1];break end end
            end
            if not hidden and floor==3 and col==cols[#cols]-1 then ty=8 end
            if not hidden and floor==2 and col==4 then ty=8 end
            local rooms={}
            for _,room in ipairs(rows('RogueTeamRoomCfg')) do
                if room.room_type==ty and room.id<10000 then rooms[#rooms+1]=room end
            end
            assert(#rooms>0,'节点房间配置为空')
            local preferred=require('RogueTeamRoomCfg')[ty*1000+floor]
            local room=preferred and preferred.room_type==ty and preferred or pick(rooms)
            if ty==8 then room=cfg('RogueTeamRoomCfg',floor==3 and 8001 or 8002) end
            if hidden and col==cols[#cols] then ty=8;room=cfg('RogueTeamRoomCfg',8004) end
            if ty==4 and r.boss_room then room=cfg('RogueTeamRoomCfg',r.boss_room) end
            local param=0
            if type(room.params)=='table' and #room.params>0 then param=pick(room.params) end
            local id=floor*10000+(hidden and 5000 or 0)+col*10+row
            local n={node_id=id,col=col,row=row,node_type=ty,next_id_list=arr(),state=col==1 and 0 or 2,param=param,room_id=room.id}
            result[#result+1]=n;bycol[col][#bycol[col]+1]=n
        end
    end
    for i,col in ipairs(cols) do for _,n in ipairs(bycol[col]) do
        for _,nextnode in ipairs(bycol[cols[i+1]] or {}) do if math.abs(nextnode.row-n.row)<=1 or #bycol[col]==1 or #bycol[cols[i+1]]==1 then n.next_id_list[#n.next_id_list+1]=nextnode.node_id end end
    end end
    return result
end
-- 按协议去掉服务器节点的内部字段。
local function wire_nodes(list)
    local out=arr()
    for _,n in ipairs(list or {}) do out[#out+1]={node_id=n.node_id,col=n.col,row=n.row,node_type=n.node_type,next_id_list=n.next_id_list,state=n.state,param=n.param} end
    return out
end
-- 结束一个节点，只解锁相邻下一列，阻止越级和重复奖励。
local function finish()
    local n=node(r.selected);assert(n.state==0 or n.state==1,'当前节点已完成')
    n.state=3;r.selected=0;stat(2,1)
    for _,other in ipairs(nodes()) do
        if other.col==n.col and other.node_id~=n.node_id then other.state=2 end
        if has(n.next_id_list,other.node_id) then other.state=0 end
    end
    if #n.next_id_list==0 then
        if r.hidden then r.switch=3
        elseif r.floor<3 then r.switch=1;stat(1,1)
        else r.floor_state=1;stat(1,1);r.ending=r.ending>0 and r.ending or 1;collect(5,r.ending) end
    end
end
-- 校验选人，分别支持已拥有角色和本模板允许的标准试用角色。
local function check_heroes(list,recruit)
    assert(type(list)=='table' and #list>0 and #list<=3,'队伍人数必须为一至三人')
    local seen={}
    for _,hero in ipairs(list) do
        local temp=hero.temp_id or 0
        assert(not seen[hero.hero_id] and hero.hp_ratio==10000,'角色重复或初始生命值无效')
        if temp~=0 then
            assert(has(template.hero_list,temp) and cfg('HeroStandardSystemCfg',temp).hero_id==hero.hero_id,'试用角色模板无效')
        else assert(has(context.owned,hero.hero_id),'角色未拥有') end
        seen[hero.hero_id]=true
        if recruit then
            for _,old in ipairs(r.heroes) do assert(old.hero_id~=hero.hero_id,'角色已经招募') end
        end
    end
end
-- 初始化新局或修复旧版只保存难度而没有地图的存档。
local function start(difficulty,heroes,affixes)
    local d=cfg('RogueTeamDifficultyCfg',difficulty)
    assert(d.template_id==template.difficulty_temp,'难度不属于当前模板')
    check_heroes(heroes,false)
    local seen={}
    for _,id in ipairs(affixes or {}) do assert(not seen[id] and has(d.affix_pool,id),'词缀不属于当前难度');seen[id]=true end
    s.serial=(s.serial or 0)+1;s.difficult=difficulty;s.hero_list=copy(heroes);s.affix_pool_id_list=arr(copy(affixes or {}))
    r={serial=s.serial,rng=(context.now+context.uid*97+s.serial*131)%2147483646+1,
        started=context.now,seconds=0,floor=1,floor_state=0,selected=0,heroes=arr(copy(heroes)),attrs={},
        treasures=arr(),items=arr(),effects=arr(),queue=arr(),stats={},visits={},options={},effect_ticks={},ending=0,ending_state=0,plot_id=0,second_map=arr(),event_id=0}
    s.run=r
    for _,row in ipairs(rows('RogueTeamAttributeCfg')) do r.attrs[tostring(row.id)]=row.default end
    r.map=make_map(1,false)
end
-- 恢复缺少局内地图的旧存档，保留图鉴、科技和历史记录。
local function migrate()
    if s.difficult>0 and not r then start(s.difficult,s.hero_list or {},s.affix_pool_id_list or {}) end
end
-- 构造局外状态；结算后下次登录不再误入旧局。
local function outside()
    return {template_id=s.template_id,difficult=s.difficult,collection_list=s.collection_list,
        his_difficult=s.his_difficult,his_avg=s.his_avg,unlock_collection=s.unlock_collection,
        view_collection=s.view_collection,tree_list=s.tree_list,last_id=s.last_id}
end
-- 效果协议仅包含客户端字段，不发送服务端用于撤销属性的内部记录。
local function wire_effects(list)
    local result=arr()
    for _,v in ipairs(list) do
        result[#result+1]={effect_id=v.effect_id,source_type=v.source_type,source_id=v.source_id,
            calc_value=v.calc_value,moment_time=v.moment_time,left_time=v.left_time,trigger_cd=v.trigger_cd}
    end
    return result
end
-- 恢复地图必须早于会立即跳转页面的成功响应。
local function snapshot(full)
    if not r or r.settled then return end
    local empty={event_type=0,param_list=arr(),drop_type=0,shop_refresh_times=0}
    if full then emit(88001,{floor_num=r.floor,floor_state=r.floor_state,select_node_id=r.selected,
        map_info=wire_nodes(r.map),second_map_info=r.hidden and wire_nodes(r.second_map) or arr(),
        treasure_list=r.treasures,other_item_list=r.items,effect_list=wire_effects(r.effects),
        template_id=s.template_id,other_info=r.pending or empty,shop_info=r.shop,
        affix_list=s.affix_pool_id_list or arr(),plot_id=r.plot_id})
    else
        emit(88003,{map_info=wire_nodes(r.map),second_map_info=r.hidden and wire_nodes(r.second_map) or arr()})
        if r.pending then emit(88015,{other_info=r.pending}) end
        if r.shop then emit(88027,{shop_info=r.shop}) end
        emit(88017,{effect_list=wire_effects(r.effects)})
    end
    local attrs=arr();for _,row in ipairs(rows('RogueTeamAttributeCfg')) do attrs[#attrs+1]={attr_id=row.id,value=attr(row.id)} end
    emit(88013,{attr_list=attrs});emit(88019,{hero_list=r.heroes,modify_type=1})
    emit(88029,{template_id=s.template_id,finish_guide=1});emit(88211,{id=r.suit_id or 0,value=r.suit_value or 0})
    if r.ending>0 then emit(88217,{id=r.ending,state=r.ending_state}) end
    if r.switch then emit(88221,{sign=r.switch}) end
    if r.event then emit(88023,copy(r.event)) end
    if r.floor_state~=0 then emit(88011,{floor_state=r.floor_state}) end
end
'''
