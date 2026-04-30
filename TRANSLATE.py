import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAME = 'HVFAN_Merged_Geely_Foton_FAW_Master.dbc'
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

@st.cache_resource
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        try:
            return cantools.database.load_file(DBC_FILENAME, encoding='gbk')
        except:
            return cantools.database.load_file(DBC_FILENAME, encoding='utf-8')
    return None

# ===================== 2. 解析引擎 (含强制关联补丁) =====================
def process_asc(file_content, db):
    data_dict = {}
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue
            
    # --- 核心补丁：建立万能 ID 映射表 ---
    # 1. 放入 DBC 的原始映射
    master_id_map = {m.frame_id: m for m in db.messages}
    
    # 2. 针对 PMS_HVFMCtrl (18FF9027) 的强行手动修复
    try:
        target_msg = db.get_message_by_name('PMS_HVFMCtrl')
        # 强制将报文 ID 映射到这个消息对象
        master_id_map[0x18FF9027] = target_msg
        master_id_map[0x98FF9027] = target_msg
        master_id_map[0x19019027] = target_msg # DBC 原始 ID 对应的 29 位形态
    except Exception as e:
        st.sidebar.error(f"补丁加载失败: {e}")

    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                cid = int(m.group('id'), 16)
                
                # 优先级查找：手动补丁 > 原始 ID > 29位掩码 ID
                msg = master_id_map.get(cid) or master_id_map.get(cid & 0x1FFFFFFF)
                
                if msg:
                    raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                    # 鲁棒性：自动对齐长度，防止数据不足报错
                    if len(raw) < msg.length:
                        raw.extend([0] * (msg.length - len(raw)))
                        
                    decoded = msg.decode(raw)
                    for s_n, s_v in decoded.items():
                        full_n = f"{msg.name}::{s_n}"
                        if full_n not in data_dict:
                            sig_obj = msg.get_signal_by_name(s_n)
                            data_dict[full_n] = {
                                'x': [], 'y': [], 
                                'unit': sig_obj.unit or "", 
                                'label': s_n
                            }
                        data_dict[full_n]['x'].append(t)
                        data_dict[full_n]['y'].append(s_v)
            except:
                continue
    return data_dict

# ===================== 3. UI 渲染 =====================
db = load_dbc()
st.title("🚗 HVFAN 综合分析系统 (全协议兼容版)")

if not db:
    st.error(f"❌ 缺失 DBC 文件: {DBC_FILENAME}")
else:
    uploaded_file = st.file_uploader("📂 选择报文文件", type=None)

    if uploaded_file:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在应用 ID 补丁并解析...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 解析失败。请检查 ASC 文件 ID 是否为 18FF9027。")
        else:
            st.success(f"✅ 解析成功！已识别信号: {len(full_data)}")
            
            # --- 控制面板 ---
            all_sig_names = sorted(full_data.keys())
            # 自动寻找关键信号
            default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "IInput", "Temp", "ModeReq"])]
            
            selected_sigs = st.multiselect("📌 信号管理", options=all_sig_names, default=default_sigs[:5])
            
            c1, c2 = st.columns(2)
            sync_on = c1.toggle("🔗 开启同步缩放", value=True)
            show_measure = c2.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    # 动态抽稀
                    step = max(1, len(d['x']) // 10000)
                    charts_to_render.append({
                        "id": f"ch_{hash(name)}",
                        "title": f"{name} ({d['unit']})",
                        "x": d['x'][::step], "y": d['y'][::step]
                    })

                js_logic = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-wrapper"></div>
                <script>
                    const data = {json.dumps(charts_to_render)};
                    const wrapper = document.getElementById('chart-wrapper');
                    const ids = [];
                    let relayouting = false;

                    data.forEach(item => {{
                        const div = document.createElement('div');
                        div.id = item.id;
                        div.style.height = '350px';
                        div.style.marginBottom = '15px';
                        wrapper.appendChild(div);
                        ids.push(item.id);

                        const layout = {{
                            title: item.title,
                            template: 'plotly_white',
                            margin: {{t:40, b:30, l:50, r:20}},
                            hovermode: "{'x unified' if show_measure else 'closest'}",
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot' }}
                        }};

                        Plotly.newPlot(item.id, [{{x: item.x, y: item.y, mode: 'lines'}}], layout, {{responsive: true}});

                        if ({str(sync_on).lower()}) {{
                            div.on('plotly_relayout', (ed) => {{
                                if (relayouting) return;
                                relayouting = true;
                                const update = {{}};
                                if (ed['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = ed['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = ed['xaxis.range[1]'];
                                }} else if (ed['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}
                                const ps = ids.map(id => id !== item.id ? Plotly.relayout(id, update) : null);
                                Promise.all(ps).finally(() => relayouting = false);
                            }});
                        }}
                    }});
                </script>
                """
                components.html(js_logic, height=len(selected_sigs)*370 + 50)
