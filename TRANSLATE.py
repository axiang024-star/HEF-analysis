import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
st.set_page_config(page_title="HVFAN 综合分析系统 (多DBC合并版)", layout="wide")

# ===================== 2. 解析引擎 (路径增强与合并版) =====================

@st.cache_resource
def load_all_local_dbcs():
    """
    自动扫描当前脚本同级目录下所有的 .dbc 文件并合并为一个数据库
    """
    db = cantools.database.Database()
    # 关键：动态获取当前文件绝对路径
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 扫描目录下所有以 .dbc 结尾的文件
    dbc_files = [f for f in os.listdir(base_dir) if f.lower().endswith('.dbc')]
    
    if not dbc_files:
        return None, []

    success_files = []
    for file_name in dbc_files:
        file_path = os.path.join(base_dir, file_name)
        try:
            # 尝试多种编码加载 DBC
            try:
                db.add_dbc_file(file_path, encoding='gbk')
            except:
                db.add_dbc_file(file_path, encoding='utf-8')
            success_files.append(file_name)
        except Exception as e:
            st.error(f"解析 {file_name} 失败: {e}")
            
    return db, success_files

def process_asc(file_content, db):
    """
    ASC 报文解析逻辑 (保持原版正则与逻辑)
    """
    data_dict = {}
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
                # 从合并后的 DB 中检索 ID
                msg = db.get_message_by_frame_id(cid)
                decoded = msg.decode(raw)
                for s_n, s_v in decoded.items():
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

# ===================== 3. UI 交互逻辑 =====================

# 1. 自动加载本地协议库
db, dbc_list = load_all_local_dbcs()

st.title("🚗 HVFAN 报文分析 (多DBC合并版)")

# 侧边栏状态指示
with st.sidebar:
    st.header("📂 已加载协议库")
    if not db:
        st.error("❌ 未在仓库根目录检测到任何 .dbc 文件")
        st.info("请检查文件名后缀是否为小写 .dbc 并提交至 GitHub")
    else:
        st.success(f"✅ 已识别 {len(dbc_list)} 个协议文件")
        for name in dbc_list:
            st.caption(f"📄 {name}")
    
    st.divider()
    if st.button("♻️ 强制清除缓存并刷新"):
        st.session_state.clear()
        st.rerun()
    st.caption("HVFAN Tool v17.7 | Multi-DBC Fixed")

# 主界面逻辑
if not db:
    st.warning("⚠️ 请先将 DBC 文件上传至 GitHub 仓库根目录，然后刷新页面。")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (.asc)", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在结合多 DBC 深度解析报文...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.error("⚠️ 未能匹配到信号。请确认 ASC 内的 ID 是否包含在已加载的 DBC 协议中。")
        else:
            st.success(f"✅ 解析成功！识别到 {len(full_data)} 个信号")

            # 控制面板
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                all_sig_names = sorted(full_data.keys())
                default_sigs = [s for s in all_sig_names if any(k in s for k in ["Spd", "Current", "Volt", "Temp", "Duty"])]
                selected_sigs = st.multiselect("📌 信号选择", options=all_sig_names, default=default_sigs if default_sigs else all_sig_names[:2])
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
                    # 动态抽稀
                    limit = 10000 
                    if len(x) > limit:
                        step = len(x) // limit
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{selected_sigs.index(name)}",
                        "title": f"{name} ({d['unit']})",
                        "x": x, "y": y
                    })

                # Plotly JS 渲染
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
                    
                    chartsData.forEach((data, index) => {{
                        const div = document.createElement('div');
                        div.id = data.id;
                        div.style.marginBottom = '20px';
                        div.style.height = '350px';
                        div.style.border = '1px solid #eee';
                        div.style.borderRadius = '8px';
                        wrapper.appendChild(div);
                        chartIds.push(data.id);

                        const layout = {{
                            title: {{ text: data.title, font: {{ size: 14 }} }},
                            margin: {{ l: 50, r: 20, t: 50, b: 40 }},
                            hovermode: hoverMode,
                            template: 'plotly_white',
                            xaxis: {{ showspikes: {str(show_measure).lower()}, spikemode: 'across' }},
                            yaxis: {{ autorange: true }}
                        }};

                        Plotly.newPlot(data.id, [{{ x: data.x, y: data.y, type: 'scatter', mode: 'lines', line: {{ width: 2, color: '#174ea6' }}, name: data.title }}], layout, {{ responsive: true, displaylogo: false }});

                        if (syncEnabled) {{
                            document.getElementById(data.id).on('plotly_relayout', (ed) => {{
                                if (isRelayouting) return;
                                isRelayouting = true;
                                const update = {{}};
                                if (ed['xaxis.range[0]']) {{
                                    update['xaxis.range[0]'] = ed['xaxis.range[0]'];
                                    update['xaxis.range[1]'] = ed['xaxis.range[1]'];
                                }} else if (ed['xaxis.autorange']) {{
                                    update['xaxis.autorange'] = true;
                                }}
                                if (Object.keys(update).length > 0) {{
                                    const ps = chartIds.map(id => id !== data.id ? Plotly.relayout(id, update) : null);
                                    Promise.all(ps).then(() => {{ isRelayouting = false; }});
                                }} else {{ isRelayouting = false; }}
                            }});
                        }}
                    }});
                </script>
                """
                render_height = len(selected_sigs) * 375 + 50
                components.html(js_logic, height=render_height, scrolling=False)
