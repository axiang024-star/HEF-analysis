import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
# 请确保该文件与脚本在同一目录下
DBC_FILENAME = 'Geely_TMCU_V1.1_20250513_PrivateCAN 10.dbc'
st.set_page_config(page_title="HVFAN 报文分析系统", layout="wide")

# ===================== 2. 解析引擎 (修复版) =====================
@st.cache_resource
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        for encoding in ['gbk', 'utf-8', 'latin-1']:
            try:
                return cantools.database.load_file(DBC_FILENAME, encoding=encoding)
            except:
                continue
    return None

def process_asc(file_content, db):
    data_dict = {}
    
    # 针对 Vector ASC 格式的精确匹配正则
    # 匹配示例: 12.345678 1 18FF9027x Rx d 8 00 11 22 33 44 55 66 77
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x\s+(?:Rx|Tx)\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
    # 多编码尝试读取 ASC 内容
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data or "Tx" in text_data: 
                break
        except: 
            continue
            
    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t = float(m.group('time'))
                # 获取原始 ID (十六进制)
                raw_id = int(m.group('id'), 16)
                # 数据场清理
                hex_data = m.group('data').strip().replace(' ', '')
                raw_payload = bytearray.fromhex(hex_data)
                
                # --- 关键修复：ID 路由策略 ---
                msg = None
                # 尝试三种 ID 匹配方式：原始匹配、J1939 掩码匹配、去掉优先级匹配
                for search_id in [raw_id, raw_id & 0x1FFFFFFF, raw_id & 0x00FFFFFF]:
                    try:
                        msg = db.get_message_by_frame_id(search_id)
                        if msg: break
                    except KeyError:
                        continue
                
                if not msg:
                    continue

                # 补足数据长度，防止位数不足时解析报错
                if len(raw_payload) < msg.length:
                    raw_payload = raw_payload.ljust(msg.length, b'\x00')

                # 解码信号
                decoded = msg.decode(raw_payload, decode_choices=False) # 获取原始物理值
                
                for s_n, s_v in decoded.items():
                    # 排除非数值类信号（如果有的话）
                    if not isinstance(s_v, (int, float)):
                        try:
                            s_v = float(s_v)
                        except:
                            continue

                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        sig_obj = msg.get_signal_by_name(s_n)
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': sig_obj.unit if sig_obj.unit else "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: 
                continue
                
    return data_dict

# ===================== 3. UI 交互与逻辑控制 =====================
db = load_dbc()
st.title("🚗 HVFAN 报文高级分析 (修复版)")

if not db:
    st.error(f"❌ 未找到 DBC 文件: {DBC_FILENAME}。请确保文件放在代码同级目录。")
else:
    st.info(f"✅ 已加载 DBC: {DBC_FILENAME} (包含 {len(db.messages)} 条报文定义)")
    
    uploaded_file = st.file_uploader("📂 上传 ASC 原始报文文件", type=['asc', 'txt'])

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在深度匹配 DBC 与报文 ID...'):
                content = uploaded_file.read()
                st.session_state.full_data = process_asc(content, db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到信号。原因可能是：\n1. ASC 文件中不包含 DBC 定义的 ID。\n2. ASC 格式非标准 Vector 格式。\n3. ID 偏移或掩码不匹配。")
        else:
            st.success(f"📈 解析完成！在报文中匹配到 {len(full_data)} 个有效信号")

            # --- 交互面板 ---
            with st.expander("🛠️ 信号显示设置", expanded=True):
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    all_sig_names = sorted(full_data.keys())
                    # 自动勾选包含关键字的信号
                    keywords = ["Spd", "Temp", "Volt", "Current", "Duty", "Target", "Act"]
                    default_sigs = [s for s in all_sig_names if any(k.lower() in s.lower() for k in keywords)]
                    
                    selected_sigs = st.multiselect(
                        "选择要显示的信号:", 
                        options=all_sig_names, 
                        default=default_sigs if default_sigs else all_sig_names[:1]
                    )
                with c2:
                    sync_on = st.toggle("🔗 同步缩放", value=True)
                with c3:
                    show_measure = st.toggle("📏 测量辅助线", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    # 针对超大数据量进行前端保护（抽稀）
                    x, y = d['x'], d['y']
                    if len(x) > 20000:
                        step = len(x) // 20000
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{hash(name)}",
                        "title": f"{name} ({d['unit']})",
                        "x": x,
                        "y": y
                    })

                # --- Plotly 多图渲染引擎 ---
                js_logic = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-container"></div>
                <script>
                    const chartsData = {json.dumps(charts_to_render)};
                    const syncEnabled = {str(sync_on).lower()};
                    const hoverMode = "{'x unified' if show_measure else 'closest'}";
                    const chartIds = [];
                    let isRelayouting = false;

                    const container = document.getElementById('chart-container');
                    
                    chartsData.forEach((data) => {{
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '15px';
                        div.style.height = '350px';
                        container.appendChild(div);
                        chartIds.push(data.id);

                        const trace = {{
                            x: data.x, y: data.y,
                            type: 'scatter', mode: 'lines',
                            line: {{ width: 1.5, color: '#2b6cb0' }},
                            name: data.title
                        }};

                        const layout = {{
                            title: {{ text: data.title, font: {{ size: 13, color: '#4a5568' }} }},
                            margin: {{ l: 60, r: 30, t: 40, b: 40 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: true, spikemode: 'across', spikedash: 'dot', color: '#718096' }},
                            yaxis: {{ autorange: true, color: '#718096', gridcolor: '#edf2f7' }}
                        }};

                        Plotly.newPlot(data.id, [trace], layout, {{ responsive: true, displaylogo: false }});

                        if (syncEnabled) {{
                            document.getElementById(data.id).on('plotly_relayout', (eventData) => {{
                                if (isRelayouting) return;
                                isRelayouting = true;
                                const update = {{}};
                                if (eventData['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = eventData['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = eventData['xaxis.range[1]'];
                                }} else if (eventData['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}

                                if (Object.keys(update).length > 0) {{
                                    const promises = chartIds.map(id => {{
                                        if (id !== data.id) return Plotly.relayout(id, update);
                                    }});
                                    Promise.all(promises).then(() => {{ isRelayouting = false; }});
                                } else {{
                                    isRelayouting = false;
                                }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 370 + 100
                components.html(js_logic, height=render_height, scrolling=False)

# 侧边栏辅助功能
with st.sidebar:
    st.header("⚙️ 设置")
    if st.button("♻️ 清除内存重读文件"):
        st.session_state.clear()
        st.rerun()
    st.divider()
    st.caption("解析逻辑：DBC(Intel/Standard) -> ASC(J1939 Extended)")
