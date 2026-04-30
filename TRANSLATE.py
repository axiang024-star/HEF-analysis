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

# ===================== 2. 深度重构解析引擎 =====================
def process_asc(file_content, db):
    data_dict = {}
    
    # 增强版正则：兼容所有空格对齐，ID 匹配部分不区分大小写
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+'
        r'(?P<channel>\d+)\s+'
        r'(?P<id>[0-9A-Fa-f]+)x?\s+'
        r'Rx\s+d\s+'
        r'(?P<dlc>\d+)\s+'
        r'(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE | re.IGNORECASE
    )
    
    # 自动识别编码
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue

    # --- 核心映射逻辑优化 ---
    # 1. 建立基础 ID 映射（包括 29位掩码后的 ID）
    master_id_map = {}
    for m in db.messages:
        master_id_map[m.frame_id] = m
        master_id_map[m.frame_id & 0x1FFFFFFF] = m

    # 2. 【核心修复】：找到 PMS_HVFMCtrl 对应的那个真正“模版”
    # 如果通过名字找不准，我们尝试通过它最可能的 ID 去反向锁定消息对象
    pms_template = None
    # 尝试所有可能的 ID 变体来获取消息对象
    for target_id in [0x18FF9027, 0x18748A00, 0x18FF4019]:
        if target_id in master_id_map:
            pms_template = master_id_map[target_id]
            break
    
    # 3. 【强行绑定】：把你要的所有 ID 全部指向那个模版
    if pms_template:
        force_ids = [0x18FF9027, 0x18748A00, 0x98FF9027, 0x98748A00]
        for fid in force_ids:
            master_id_map[fid] = pms_template
    else:
        st.sidebar.error("DBC 中未找到 PMS 相关的 ID 定义，请检查 DBC 内容")

    # 逐行解析
    for line in text_data.splitlines():
        line = line.strip()
        if not line: continue
        
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                # 统一转为大写 ID 进行逻辑匹配
                raw_id_hex = m.group('id').upper()
                cid = int(raw_id_hex, 16)
                
                # 匹配逻辑：优先找补丁映射，再找掩码映射
                msg = master_id_map.get(cid) or master_id_map.get(cid & 0x1FFFFFFF)
                
                if msg:
                    raw_hex = m.group('data').strip().replace(' ', '')
                    raw_bytes = bytearray.fromhex(raw_hex)
                    
                    # 鲁棒性：自动对齐长度
                    if len(raw_bytes) < msg.length:
                        raw_bytes.extend([0] * (msg.length - len(raw_bytes)))
                    
                    # 执行解码
                    decoded = msg.decode(raw_bytes[:msg.length])
                    for s_n, s_v in decoded.items():
                        full_name = f"{msg.name}::{s_n}"
                        if full_name not in data_dict:
                            try:
                                sig_obj = msg.get_signal_by_name(s_n)
                                unit = sig_obj.unit or ""
                            except: unit = ""
                            data_dict[full_name] = {'x': [], 'y': [], 'unit': unit}
                        
                        data_dict[full_name]['x'].append(t)
                        data_dict[full_name]['y'].append(s_v)
            except:
                continue
    return data_dict

# ===================== 3. UI 逻辑 =====================
db = load_dbc()
st.title("🚗 HVFAN 深度协议解析系统")

if not db:
    st.error(f"❌ 缺失 DBC 文件: {DBC_FILENAME}")
else:
    uploaded_file = st.file_uploader("📂 上传报文", type=None)

    if uploaded_file:
        file_key = f"{uploaded_file.name}_{uploaded_file.size}"
        if 'full_data' not in st.session_state or st.session_state.get('file_key') != file_key:
            with st.spinner('🚀 正在执行多 ID 联动解析...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.file_key = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未识别到信号。请检查 ASC 文件中 ID 后的后缀是否为 'x' 且包含 'Rx d'。")
        else:
            st.success(f"✅ 解析成功！已提取信号: {len(full_data)}")
            
            all_sigs = sorted(full_data.keys())
            # 搜索默认显示的关键信号
            default_selection = [s for s in all_sigs if any(k in s for k in ["Spd", "Temp", "IInput", "HVFMCtrl"])]
            selected = st.multiselect("📊 信号看板", options=all_sigs, default=default_selection[:6])
            
            sync_on = st.toggle("🔗 时间轴同步缩放", value=True)

            if selected:
                render_list = []
                for name in selected:
                    d = full_data[name]
                    step = max(1, len(d['x']) // 8000)
                    render_list.append({
                        "id": f"chart_{abs(hash(name))}",
                        "title": name,
                        "unit": d['unit'],
                        "x": d['x'][::step], "y": d['y'][::step]
                    })

                js_code = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="viz-container"></div>
                <script>
                    const chartData = {json.dumps(render_list)};
                    const container = document.getElementById('viz-container');
                    const ids = [];
                    let syncing = false;

                    chartData.forEach(item => {{
                        const d = document.createElement('div');
                        d.id = item.id;
                        d.style.height = '300px';
                        d.style.marginBottom = '10px';
                        container.appendChild(d);
                        ids.push(item.id);

                        Plotly.newPlot(item.id, [{{ 
                            x: item.x, y: item.y, mode: 'lines', 
                            line: {{width: 1}} 
                        }}], {{
                            title: {{ text: item.title + ' (' + item.unit + ')', font: {{size: 13}} }},
                            margin: {{ t: 35, b: 30, l: 50, r: 20 }},
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot' }},
                            hovermode: 'x unified',
                            template: 'plotly_white'
                        }}, {{responsive: true}});

                        if ({str(sync_on).lower()}) {{
                            d.on('plotly_relayout', (ed) => {{
                                if (syncing) return;
                                syncing = true;
                                const update = {{}};
                                if (ed['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = ed['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = ed['xaxis.range[1]'];
                                }} else if (ed['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}
                                const ps = ids.map(id => id !== item.id ? Plotly.relayout(id, update) : null);
                                Promise.all(ps).finally(() => syncing = false);
                            }});
                        }}
                    }});
                </script>
                """
                components.html(js_code, height=len(selected)*320 + 50)
