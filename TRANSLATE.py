import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
# 不再硬编码单一 DBC 文件名，改为自动扫描
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (增强：多 DBC 自动合并) =====================
@st.cache_resource
def load_combined_db():
    """扫描当前目录所有DBC并合并为一个数据库对象"""
    dbc_files = [f for f in os.listdir('.') if f.lower().endswith('.dbc')]
    
    if not dbc_files:
        return None, []

    combined_db = cantools.database.Database()
    loaded_successfully = []

    for dbc_file in dbc_files:
        try:
            # 尝试 GBK 和 UTF-8 编码加载
            try:
                temp_db = cantools.database.load_file(dbc_file, encoding='gbk')
            except:
                temp_db = cantools.database.load_file(dbc_file, encoding='utf-8')
            
            # 核心修改：将所有消息合并至主库
            for msg in temp_db.messages:
                # 注意：如果ID重复，此处add_message会以最后加载的为准
                combined_db.add_message(msg)
            loaded_successfully.append(dbc_file)
        except:
            continue
            
    return combined_db, loaded_successfully

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
                # 从合并后的数据库中按 ID 获取消息定义
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

# ===================== 3. UI 交互与逻辑控制 =====================
db, loaded_dbcs = load_combined_db()
st.title("🚗 HVFAN 报文分析 (多DBC合并版 v17.6)")

# 侧边栏展示已加载的库
st.sidebar.header("📁 已加载协议库")
if loaded_dbcs:
    for f in loaded_dbcs:
        st.sidebar.caption(f"✅ {f}")
else:
    st.sidebar.error("❌ 目录下未检测到DBC文件")

if not db:
    st.error("❌ 请确保目录下至少有一个 .dbc 文件。")
else:
    uploaded_file = st.file_uploader("📂 选择并上传报文文件", type=None)

    if uploaded_file is not None:
        # v17.6 强力锁定代码：基于文件名和大小锁定，防止重复解析
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在深度解析报文...'):
                st.session_state.full_data = process_asc(uploaded_file.read(), db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能匹配到有效信号，请检查报文 ID 是否在已加载的 DBC 范围内。")
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
                    # v17.6 手机端抽稀策略：点数限制在10000以内防止卡死
                    limit = 10000 
                    if len(x) > limit:
                        step = len(x) // limit
                        x, y = x[::step], y[::step]
                    
                    charts_to_render.append({
                        "id": f"chart_{selected_sigs.index(name)}",
                        "title": f"{d['label']} ({d['unit']})",
                        "x": x,
                        "y": y
                    })

                # --- 4. 增强版 JS 渲染引擎 ---
                # 修复核心：JS内部的大括号使用 {{ }} 双重转义，解决 SyntaxError
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
if st.sidebar.button("♻️ 强制清除缓存并刷新"):
    st.session_state.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("HVFAN Tool v17.6 | Multi-DBC Support")
