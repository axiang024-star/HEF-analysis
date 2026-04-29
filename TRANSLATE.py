import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components
import io

# ===================== 1. 核心配置 =====================
st.set_page_config(page_title="HVFAN 综合分析系统 (多DBC版)", layout="wide")

# ===================== 2. 解析引擎 (增强版) =====================

@st.cache_resource
def load_combined_db(uploaded_dbc_files):
    """
    将上传的多个DBC文件合并为一个数据库
    """
    db = cantools.database.Database()
    if not uploaded_dbc_files:
        return None
    
    for dbc_file in uploaded_dbc_files:
        # 将上传的文件流转为字符串IO，因为cantools需要类文件对象
        dbc_content = dbc_file.getvalue().decode('gbk', errors='ignore')
        # 也可以尝试 utf-8，这里用 add_dbc_string
        try:
            db.add_dbc_string(dbc_content)
        except Exception as e:
            st.error(f"解析 DBC {dbc_file.name} 失败: {e}")
    return db

def process_asc(file_content, db):
    data_dict = {}
    # 兼容 Vector ASC 标准格式的正则
    frame_re = re.compile(r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', re.MULTILINE)
    
    text_data = ""
    for enc in ['utf-8', 'gbk', 'latin-1']:
        try:
            text_data = file_content.decode(enc, errors='ignore')
            if "Rx" in text_data: break
        except: continue
            
    lines = [l.strip() for l in text_data.splitlines() if l.strip()]
    for line in lines:
        m = frame_re.match(line)
        if m:
            try:
                t, cid = float(m.group('time')), int(m.group('id'), 16)
                raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                # 从合并后的数据库中查找 ID
                msg = db.get_message_by_frame_id(cid)
                decoded = msg.decode(raw)
                for s_n, s_v in decoded.items():
                    # 增加 DBC 来源标记（可选，如果需要区分同名消息）
                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': msg.get_signal_by_name(s_n).unit or "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: continue
    return data_dict

# ===================== 3. UI 交互与逻辑控制 =====================
st.title("🚗 HVFAN 报文分析 (多DBC支持版)")

# --- 侧边栏：DBC 管理 ---
st.sidebar.header("⚙️ 配置库文件")
uploaded_dbcs = st.sidebar.file_uploader("1. 上传 DBC 文件 (支持多个)", type=['dbc'], accept_multiple_files=True)

db = None
if uploaded_dbcs:
    db = load_combined_db(uploaded_dbcs)
    st.sidebar.success(f"✅ 已加载 {len(uploaded_dbcs)} 个 DBC")
else:
    st.info("💡 请先在左侧上传 DBC 数据库文件。")

# --- 主界面：ASC 上传 ---
if db:
    uploaded_file = st.file_uploader("2. 选择并上传报文文件 (ASC)", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        # 缓存逻辑：如果DBC变了或文件变了，则重新解析
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在结合多 DBC 数据库解析报文...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到有效信号。请确保 ASC 格式正确，且 ID 在上传的 DBC 中存在。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(full_data)} 个信号")

            # --- 控制面板 ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            
            with c1:
                all_sig_names = sorted(full_data.keys())
                default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp", "Duty"])]
                selected_sigs = st.multiselect(
                    "📌 信号管理", 
                    options=all_sig_names, 
                    default=default_sigs if default_sigs else all_sig_names[:2]
                )
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 数据抽稀
                    limit = 10000 
                    if len(x) > limit:
                        step = len(x) // limit
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{selected_sigs.index(name)}",
                        "title": f"{name} ({d['unit']})",
                        "x": x,
                        "y": y
                    })

                # --- JS 渲染引擎 ---
                js_logic = f"""
                <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
                <div id="chart-wrapper"></div>
                <script>
                    const chartsData = {json.dumps(charts_to_render)};
                    const syncEnabled = {str(sync_on).lower()};
                    const hoverMode = "{'x unified' if show_measure else 'closest'}";
                    const chartIds = [];
                    let isRelayouting = false;

                    const wrapper = document.getElementById('chart-wrapper');
                    wrapper.innerHTML = ''; 
                    
                    chartsData.forEach((data, index) => {{
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '20px';
                        div.style.height = '350px';
                        wrapper.appendChild(div);
                        chartIds.push(data.id);

                        const trace = {{
                            x: data.x, y: data.y,
                            type: 'scatter', mode: 'lines',
                            line: {{ width: 2, color: '#174ea6' }},
                            name: data.title
                        }};

                        const layout = {{
                            title: {{ text: data.title, font: {{ size: 14 }} }},
                            margin: {{ l: 50, r: 20, t: 50, b: 40 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: {str(show_measure).lower()}, spikemode: 'across', spikedash: 'dot' }},
                            yaxis: {{ autorange: true }}
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
                                }} else {{
                                    isRelayouting = false;
                                }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 375 + 50
                components.html(js_logic, height=render_height, scrolling=False)

# 侧边栏清理
if st.sidebar.button("♻️ 强制清除缓存"):
    st.session_state.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("HVFAN Multi-DBC v1.0")
