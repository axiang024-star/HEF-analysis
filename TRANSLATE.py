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

# ===================== 2. 增强型解析引擎 =====================
def process_asc(file_content, db):
    data_dict = {}
    
    # 增强版正则：兼容行首空格和 ID 与 Rx 之间的长间隙 (\s+)
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+'
        r'(?P<channel>\d+)\s+'
        r'(?P<id>[0-9A-Fa-f]+)x?\s+'
        r'Rx\s+d\s+'
        r'(?P<dlc>\d+)\s+'
        r'(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
    # 编码自动识别
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue
            
    # --- 核心映射表构建 ---
    master_id_map = {m.frame_id: m for m in db.messages}
    
    # 【用户指定修正】强制将 18FF9027 映射到 PMS_HVFMCtrl
    try:
        target_msg = db.get_message_by_name('PMS_HVFMCtrl')
        # 强制覆盖 ID 映射逻辑
        master_id_map[0x18FF9027] = target_msg
        master_id_map[0x98FF9027] = target_msg # 兼容带有优先级掩码的 ID
    except Exception as e:
        st.sidebar.error(f"信号映射失败: 找不到消息 'PMS_HVFMCtrl'")

    # 逐行扫描解析
    lines = text_data.splitlines()
    for line in lines:
        line = line.strip()
        if not line: continue
        
        match = frame_re.match(line)
        if match:
            try:
                timestamp = float(match.group('time'))
                raw_id = int(match.group('id'), 16)
                
                # 查找消息定义：先查映射表，再查 29位掩码
                msg = master_id_map.get(raw_id) or master_id_map.get(raw_id & 0x1FFFFFFF)
                
                if msg:
                    raw_hex = match.group('data').strip().replace(' ', '')
                    raw_bytes = bytearray.fromhex(raw_hex)
                    
                    # 长度自动补齐 (防止 ASC DLC 小于 DBC 定义时报错)
                    if len(raw_bytes) < msg.length:
                        raw_bytes.extend([0] * (msg.length - len(raw_bytes)))
                        
                    # 解码信号
                    decoded = msg.decode(raw_bytes[:msg.length])
                    for sig_name, sig_val in decoded.items():
                        full_name = f"{msg.name}::{sig_name}"
                        if full_name not in data_dict:
                            try:
                                sig_obj = msg.get_signal_by_name(sig_name)
                                unit = sig_obj.unit or ""
                            except:
                                unit = ""
                            data_dict[full_name] = {
                                'x': [], 'y': [], 
                                'unit': unit, 
                                'label': sig_name
                            }
                        data_dict[full_name]['x'].append(timestamp)
                        data_dict[full_name]['y'].append(sig_val)
            except:
                continue
    return data_dict

# ===================== 3. Streamlit UI 逻辑 =====================
db = load_dbc()
st.title("🚗 HVFAN 综合分析系统")

if not db:
    st.error(f"❌ 未找到 DBC 文件: {DBC_FILENAME}")
else:
    uploaded_file = st.file_uploader("📂 上传 ASC 报文文件", type=['asc', 'txt'])

    if uploaded_file:
        file_key = f"{uploaded_file.name}_{uploaded_file.size}"
        if 'full_data' not in st.session_state or st.session_state.get('file_key') != file_key:
            with st.spinner('🚀 正在解析报文并应用 18FF9027 映射补丁...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.file_key = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到有效信号。请确认 ASC 内容符合 Vector 格式且包含 Rx 标志。")
        else:
            st.success(f"✅ 解析完成！找到信号: {len(full_data)}")
            
            # --- 交互式控制 ---
            all_sigs = sorted(full_data.keys())
            # 预设常用观测信号
            default_selection = [s for s in all_sigs if any(k in s for k in ["Spd", "Temp", "IInput", "HVFMCtrl"])]
            
            selected = st.multiselect("📊 选择观测信号", options=all_sigs, default=default_selection[:6])
            
            c1, c2 = st.columns(2)
            sync_on = c1.toggle("🔗 时间轴同步", value=True)
            measure_on = c2.toggle("📏 显示测量线", value=True)

            if selected:
                render_list = []
                for name in selected:
                    d = full_data[name]
                    # 动态降采样提升渲染性能
                    step = max(1, len(d['x']) // 10000)
                    render_list.append({
                        "id": f"chart_{abs(hash(name))}",
                        "title": f"{name} ({d['unit']})",
                        "x": d['x'][::step], "y": d['y'][::step]
                    })

                # JavaScript 联动绘图逻辑
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
                        d.style.height = '320px';
                        d.style.marginBottom = '15px';
                        container.appendChild(d);
                        ids.push(item.id);

                        const layout = {{
                            title: {{ text: item.title, font: {{size: 14}} }},
                            template: 'plotly_white',
                            margin: {{ t: 40, b: 30, l: 60, r: 20 }},
                            hovermode: "{'x unified' if measure_on else 'closest'}",
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot' }}
                        }};

                        Plotly.newPlot(item.id, [{{ x: item.x, y: item.y, mode: 'lines' }}], layout, {{responsive: true}});

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
                components.html(js_code, height=len(selected)*350 + 50)
