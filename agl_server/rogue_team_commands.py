"""虚构推演请求分发与战斗、结算规则。"""

COMMANDS = r'''
-- 构造累计积分推送，奖励领取记录由同一存档维护。
local function score_push()
    emit(88309,{template_id=s.template_id,point=s.point,reward_list=s.reward_list})
    for _,id in ipairs(cfg('ActivityCfg',template.activity_id).sub_activity_list) do
        if cfg('ActivityCfg',id).activity_template==82 then emit(60201,{activity_id=id,point=s.point,reward_list=s.reward_list}) end
    end
end
-- 保存通关次数，重复结算不会重复增加历史数据。
local function history(list,key)
    for _,row in ipairs(list) do if row.key==key then row.value=row.value+1;return end end
    list[#list+1]={key=key,value=1}
end
-- 一次性结算本局统计与真实账号积分、科技材料。
local function settle()
    assert(r,'没有可结算的推演')
    if r.result then return r.result end
    if r.floor_state==0 then r.floor_state=2 end
    local sum=0;local values=arr()
    local settings={14001,14002,14003,14004,14005,14006,14007,14008}
    for id,setting_id in ipairs(settings) do local value=r.stats[tostring(id)] or 0;values[#values+1]={key=id,value=value};sum=sum+value*setting(setting_id)[1] end
    local difficulty=cfg('RogueTeamDifficultyCfg',s.difficult)
    local weight=0;for _,id in ipairs(s.affix_pool_id_list) do weight=weight+cfg('ActivityAffixPoolCfg',id).point end
    local factor=math.min(difficulty.score+weight*setting(27001)[1],setting(27002)[1])
    local total=math.floor(sum*factor/100)
    values[#values+1]={key=100,value=sum};values[#values+1]={key=101,value=total}
    local point=math.floor(total*setting(15001)[1]/1000);local tech=math.floor(total*setting(15002)[1]/1000)
    if point>0 then out.rewards[#out.rewards+1]={id=template.point_item,num=point};s.point=s.point+point end
    if tech>0 then out.rewards[#out.rewards+1]={id=template.tech_item,num=tech} end
    if r.floor_state==1 then history(s.his_difficult,s.difficult);history(s.his_avg,r.ending) end
    r.result={result=0,end_info_list=values,collection_list=copy(s.collection_list),unlock_collection=copy(s.unlock_collection),total_time=r.seconds}
    r.settled=true;s.difficult=0
    return r.result
end
-- 检查战斗属于当前局、当前节点和尚未结算的路线。
local function validate_battle()
    assert(r and request.serial==r.serial,'推演已重开，本场战斗不能结算')
    if request.cached then return end
    active();assert(r.selected==request.node_id and node(request.node_id).state==0,'当前节点没有进行中的战斗')
end
-- 选择节点并按房间类型准备交互，不直接生成战斗胜利。
local function select_node(id)
    active();assert(r.floor_state==0 and not r.switch,'本层已结束')
    assert(not r.pending and not r.shop and not r.event,'请先处理当前事件')
    local n=node(id);assert(n.state==0 and (r.selected==0 or r.selected==id),'节点尚未解锁或已有进行中节点')
    if r.selected==id then return end
    r.selected=id;trigger(20)
    if n.node_type>=1 and n.node_type<=2 and has(r.items,1310046) and (r.effect_ticks['1310046'] or 0)<2 then
        local room=cfg('RogueTeamRoomCfg',2000+r.floor);n.node_type=2;n.room_id=room.id;n.param=pick(room.params)
    end
    if n.node_type==2 then trigger(13) end
    if n.node_type==5 then trigger(21);shop();tick_item(1200001,1)
    elseif n.node_type==6 then trigger(22);event(n.param)
    elseif n.node_type==7 or n.node_type==8 or n.node_type==9 then
        if n.node_type==7 then r.extra_events=attr(27);tick_item(1200002,1) end
        if n.node_type==9 and has(r.items,1200004) then effects(cfg('RogueTeamItemCfg',1200004).effect_id_list,23,1,1200004,0);tick_item(1200004,1) end
        event(n.param)
    elseif n.node_type==10 then finish() end
end
if command=='pushes' then
    migrate();snapshot(true);score_push()
    -- 88033 会访问战斗页创建的编队代理，登录恢复不能重放旧战斗结果。
    if request.battle_result and r and r.last_battle then emit(88033,r.last_battle) end
elseif command=='battle_pushes' then
    active();snapshot(true)
elseif command==88100 then
    assert(not r or r.settled,'已有进行中的推演，请继续或结算后重新开始')
    s.template_id=request.template_id;template=cfg('RogueTeamCfg',s.template_id)
    start(request.difficult,request.hero_list,request.affix_pool_id_list)
    for _,id in ipairs(s.tree_list) do effects(cfg('RogueTeamSkillTreeCfg',id).effect,1,0,id,1) end
    offer(5,choices(pool(1),3),1);emit(88305,outside());snapshot(true)
elseif command==88030 then
    assert(request.type==0 or request.type==1,'进入状态无效')
    migrate();active()
    if request.type==0 then snapshot(true) end
elseif command==88004 then
    select_node(request.node_id)
    if r.selected~=0 and node(r.selected).node_type>=5 and not r.event_battle then continue_node() end
    snapshot()
elseif command==88008 then
    active();assert(r.switch and request.sign==r.switch,'地图切换状态不匹配')
    if r.switch==1 then r.floor=r.floor+1;r.map=make_map(r.floor,false);r.second_map=arr();r.hidden=false
    elseif r.switch==2 then r.second_map=make_map(r.floor,true);r.hidden=true
    elseif r.switch==3 then r.hidden=false;r.second_map=arr() end
    r.switch=nil;r.selected=0;r.event=nil;r.shop=nil;snapshot(true)
elseif command==88024 then
    active();assert(r.event and not r.event_battle and not r.pending,'当前没有可操作事件选项')
    local next_id
    if request.opt_id==0 then
        assert(#r.event.opt_list==0,'必须选择有效事件选项')
    else
        local chosen
        for _,option in ipairs(r.event.opt_list) do if option.opt_id==request.opt_id then chosen=option end end
        assert(chosen,'事件选项未解锁')
        local plan=assert(r.event_plans[tostring(request.opt_id)],'事件分支已失效')
        choose_option(request.opt_id,plan);next_id=plan.next_id
    end
    if not r.event_battle then
        r.event=nil;r.event_id=0;emit(88023,{event_id=0,opt_list=arr(),trigger_type=0})
        if next_id then event(next_id)
        elseif (r.extra_events or 0)>0 then
            r.extra_events=r.extra_events-1;event(pick(cfg('RogueTeamRoomCfg',7000+r.floor).params))
        end
    end
    continue_node();snapshot()
elseif command==88200 then
    active();local pending=assert(r.pending,'没有等待选择的奖励')
    assert(request.event_id==pending.event_type,'奖励窗口已失效')
    local choice
    for _,v in ipairs(pending.param_list) do if v.index==request.param_arg then choice=v end end
    assert(choice or request.param_arg==0 and #pending.param_list==0,'奖励选项不存在')
    r.pending=nil
    if choice then
        if pending.event_type==8 then local t=assert(treasure(choice.param),'藏品不存在');t.rare=math.min(4,t.rare+1);item_push(t.id,t.rare,3)
        else obtain(choice.param,choice.rare,0) end
    end
    if pending.event_type==11 then mechanism_offer() end
    continue_node();out.reply.window_opt=true;snapshot()
elseif command==88202 then
    active();assert(r.pending and request.event_id==r.pending.event_type,'刷新窗口已失效')
    change(7,-1);local ty=r.pending.event_type
    assert(ty==1 and request.event_id==1,'该奖励不能刷新')
    local rare=r.pending.param_list[1] and r.pending.param_list[1].rare or 1
    local queued=r.queue;r.queue=arr();r.pending=nil
    offer(ty,choices(pool(4),3),rare);r.queue=queued;snapshot()
elseif command==88102 then
    active();assert(r.pending and r.pending.event_type==3,'当前没有招募事件')
    if #request.hero_list>0 then check_heroes(request.hero_list,true) end
    assert(#request.hero_list<=attr(8),'招募次数不足')
    for _,hero in ipairs(request.hero_list) do r.heroes[#r.heroes+1]=copy(hero) end
    for _,record in ipairs(r.effects) do
        local e=cfg('RogueTeamEffectCfg',record.effect_id)
        if e.action==1055 then
            local gain=math.min(e.params[2]-record.calc_value,e.params[1]*#request.hero_list)
            change(23,gain);record.calc_value=record.calc_value+gain
        end
    end
    change(8,-#request.hero_list);r.pending=nil;continue_node();snapshot()
elseif command==88212 then
    active();assert(r.pending and r.pending.event_type==9,'当前没有物品丢弃选择')
    local spec=r.pending.param_list[1];local e=cfg('RogueTeamEffectCfg',spec.param)
    assert(#request.item_id_list==spec.buy_times,'丢弃数量不匹配');local seen={}
    for _,id in ipairs(request.item_id_list) do
        assert(not seen[id],'丢弃物品重复');seen[id]=true
        if e.action==9 then local t=assert(treasure(id),'藏品未持有');assert(t.rare>=e.params[1] and t.rare<=e.params[2],'藏品品质不匹配')
        else assert(has(r.items,id) and has(e.params[1],cfg('RogueTeamItemCfg',id).sub_type),'遗物类型不匹配') end
    end
    for _,id in ipairs(request.item_id_list) do remove_item(id) end
    r.pending=nil;continue_node();snapshot()
elseif command==88208 then
    active();assert(r.pending and r.pending.event_type==10,'当前没有节点替换选择')
    local allowed=false
    for _,v in ipairs(r.pending.param_list) do if v.index==request.node_id and v.param==request.node_type then allowed=true end end
    assert(allowed,'节点不在替换范围')
    local n=node(request.node_id);local room=cfg('RogueTeamRoomCfg',request.node_type*1000+1)
    n.node_type=room.room_type;n.room_id=room.id;n.param=pick(room.params)
    r.pending=nil;continue_node();snapshot()
elseif command==88222 then
    active();local current=assert(r.shop,'当前不在商店')
    if request.index==0 then r.shop=nil;continue_node()
    else
        local product
        for _,v in ipairs(current.param_list) do if v.index==request.index then product=v end end
        assert(product and product.buy_times==0,'商品不存在或已经购买')
        local prices=cfg('RogueTeamItemCfg',product.param).price
        local price=assert(prices[product.rare] or prices[1],'商品价格不存在')
        change(6,-math.ceil(math.ceil(price*attr(11)/1000)*product.discount/100))
        product.buy_times=1;obtain(product.param,product.rare,0);trigger(23)
    end
    snapshot()
elseif command==88224 then
    active();assert(r.shop and attr(30)>0,'当前不能刷新商店')
    change(6,-setting(26001)[1]*2^r.shop.shop_refresh_times);shop(r.shop.shop_refresh_times+1);snapshot()
elseif command==88218 then
    active();assert(r.floor_state==1 and r.ending>0,'尚未达成结局');r.ending_state=1
elseif command==88306 then
    out.reply=copy(settle());score_push()
elseif command==88300 then
    assert(request.template_id==s.template_id,'科技模板不匹配')
    local tree=cfg('RogueTeamSkillTreeCfg',request.tree_id)
    assert(tree.template_id==template.tree_temp and not has(s.tree_list,tree.id),'科技不存在或已经解锁')
    for _,id in ipairs(type(tree.pre_node)=='table' and tree.pre_node or {}) do assert(has(s.tree_list,id),'前置科技未解锁') end
    assert(context.tech>=tree.cost,'科技材料不足')
    out.costs[#out.costs+1]={id=template.tech_item,num=tree.cost};s.tree_list[#s.tree_list+1]=tree.id
    emit(88305,outside())
elseif command==88312 then
    assert(request.template_id==s.template_id and (request.type==0 or request.type==1),'图鉴请求无效')
    for _,id in ipairs(request.colllection_id) do
        cfg('RogueTeamItemCfg',id)
        local found=false;for _,row in ipairs(s.collection_list) do if has(row.item_list,id) then found=true end end
        assert(found or has(s.unlock_collection,id),'图鉴尚未解锁');unique(s.view_collection,id)
    end
    emit(88305,outside())
elseif command==88316 then
    assert(request.main_id==s.template_id,'积分模板不匹配')
    local reward=cfg('ActivityPointRewardCfg',request.id)
    assert(has(cfg('ActivityCfg',template.activity_id).sub_activity_list,reward.activity_id) and cfg('ActivityCfg',reward.activity_id).activity_template==82,'积分档位不属于本玩法')
    s.last_id=request.id
elseif command=='claim' then
    local ids=request.point_reward_id_list;assert(#ids>0,'领奖列表为空');local seen={}
    out.reply.reward_list=arr()
    for _,id in ipairs(ids) do
        local c=cfg('ActivityPointRewardCfg',id)
        assert(not seen[id] and not has(s.reward_list,id) and has(cfg('ActivityCfg',template.activity_id).sub_activity_list,c.activity_id) and cfg('ActivityCfg',c.activity_id).activity_template==82,'奖励无效或已经领取')
        assert(s.point>=c.need,'推演积分不足');seen[id]=true
        for _,pair in ipairs(c.reward_item_list) do local v={id=pair[1],num=pair[2]};out.rewards[#out.rewards+1]=v;out.reply.reward_list[#out.reply.reward_list+1]=v end
    end
    for _,id in ipairs(ids) do s.reward_list[#s.reward_list+1]=id end
    score_push()
elseif command=='prepare' then
    active();assert(r.floor_state==0 and not r.pending and (not r.event or r.event_battle) and not r.shop,'当前事件尚未完成')
    local n=node(request.dest)
    assert(n.state==0 and (r.selected==0 or r.selected==n.node_id),'战斗节点尚未解锁')
    assert(n.node_type>=1 and n.node_type<=4 or r.event_battle,'当前节点不是战斗')
    assert((request.battle_times or 1)==1,'推演不支持多倍战斗')
    local seen={};local count=0
    for _,selected in ipairs(request.hero_list) do if selected.hero_id~=0 then
        local found=false
        for _,hero in ipairs(r.heroes) do
            local key=selected.hero_type==2 and hero.temp_id or hero.hero_id
            if selected.hero_id==key and hero.hp_ratio>0 and (selected.hero_type==2)==(hero.temp_id~=0) then found=true end
        end
        assert(found and not seen[selected.hero_id],'角色未招募、重复或已经阵亡');seen[selected.hero_id]=true;count=count+1
    end end
    assert(count>0 and count<=3,'战斗队伍人数无效')
    if r.selected==0 then select_node(n.node_id) end
    out.stage_id=r.event_battle and r.event_battle.stage_id or n.param
    if r.event_battle and r.event_battle.room_id==11001 then
        assert(count==1,'心魔战斗必须选择一名角色')
        local hero_id
        for _,selected in ipairs(request.hero_list) do if selected.hero_id~=0 then
            hero_id=selected.hero_type==2 and cfg('HeroStandardSystemCfg',selected.hero_id).hero_id or selected.hero_id
        end end
        out.stage_id=nil
        for _,pair in ipairs(cfg('RogueTeamRoomCfg',11001).params) do if pair[1]==hero_id then out.stage_id=pair[2] end end
        assert(out.stage_id,'该角色没有匹配的心魔关卡')
    end
    out.serial=r.serial
elseif command=='validate' then
    validate_battle()
elseif command=='complete' then
    validate_battle()
    local n=node(request.node_id);local info=request.info or {}
    -- 客户端 NetProcol.BattleResultInfo 的第九字段为 ResurrectTimes。
    local revives=info['9'] or 0
    assert(revives>=0 and revives==math.floor(revives) and revives<=attr(5),'战斗复活次数无效')
    change(4,-revives)
    for _,c in ipairs(info.characters or {}) do if (c['3'] or 0)>0 then
        for _,hero in ipairs(r.heroes) do if hero.hero_id==c['1'] or hero.temp_id==c['1'] then hero.hp_ratio=math.min(10000,math.max(0,math.floor((c['2'] or 0)*10000/c['3']))) end end
    end end
    r.seconds=r.seconds+(request.seconds or 0)
    if request.result==1 then
        local kind=r.event_battle and cfg('RogueTeamRoomCfg',r.event_battle.room_id).room_type or n.node_type
        stat(kind==1 and 6 or kind==2 and 7 or 8,1)
        local coin=math.floor(100*(attr(19)+attr(31)+attr(32))/1000)
        change(6,coin);trigger(4);if kind==2 then trigger(6) end
        r.last_battle={treasure_num=r.event_battle and r.event_battle.treasure_count or 1,relic_num=r.event_battle and r.event_battle.relic_count or 0,coin_num=coin}
        if r.event_battle then
            local plan=r.event_battle;r.event_battle=nil;plan.stage_id=nil
            choose_option(plan.opt_id,plan)
            r.event=nil;r.event_id=0
            if plan.next_id then event(plan.next_id) end
        else offer(1,choices(pool(4),3),treasure_rare()) end
        tick_item(1200003,2);tick_item(1330001,2);tick_item(1310046,2)
        n.state=r.event and 0 or 1
        continue_node()
    elseif request.result==2 then r.floor_state=2
    end
else error('未知虚构推演请求') end
refresh_unlocks()
return out
'''
