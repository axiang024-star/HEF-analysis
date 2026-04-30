import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
# 【修改】支持多 DBC 文件列表
DBC_FILENAMES = [
    'HVFAN_Merged_Geely_Foton_FAW_Master.dbc',
    'Geely_TMCU_V1.1_20250513_PrivateCAN 10.dbc',  # 在此添加更多 DBC 路径
]
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (多DBC合并与多编码支持) =====================
@st.cache_resource
def load_dbc():
    """
    加载多份DBC文件并合并到一个数据库对象中
    """
    merged_db = cantools.database.Database()
    loaded_files = []
    
    for filename in DBC_FILENAMES:
        if os.path.exists(filename):
            try:
                # 尝试用 gbk 加载
                merged_db.add_dbc_file(filename, encoding='gbk')
                loaded_files.append(filename)
            except:
                try:
                    # 尝试用 utf-8 加载
                    merged_db.add_dbc_file(filename, encoding='utf-8')
                    loaded_files.append(filename)
                except Exception as e:
                    st.warning(f"⚠️ 文件 {filename} 解析失败: {str(e)}")
        else:
            st.warning(f"❓ 找不到文件: {filename}")
            
    return merged_db if loaded_files else None

def process_asc(file_content, db):
    data_dict = {}
    
    # 正则表达式：保持原有的 Rx|Tx 兼容性
    frame_re = re.compile(
        r'^\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+(?:Rx|Tx)\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)', 
        re.MULTILINE
    )
    
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
                t, cid = float(m.group('time')), int(m.group('id'), 16)
                raw = bytearray.fromhex(m.group('data').replace(' ', ''))
                
                # 从合并后的数据库查找 ID
                msg = db.get_message_by_frame_id(cid)
                decoded = msg.decode(raw)
                
                for s_n, s_v in decoded.items():
                    # 增加 DBC 来源标记（可选）：f"[{msg.bus_name}] {msg.name}::{s_n}"
                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        data_dict[full_n] = {
                            'x': [], 'y': [], 
                            'unit': msg.get_signal_by_name(s_n).unit or "",
                            'label': s_n
                        }
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except: 
                continue
    return data_dict

# ===================== 3. UI 交互与逻辑控制 =====================
db = load_dbc()
st.title("🚗 HVFAN 报文分析 (多DBC集成版)")

if not db:
    st.error(f"❌ 无法加载定义的 DBC 文件列表。请确认文件是否存在。")
else:
    # 侧边栏显示已加载库的信息
    st.sidebar.info(f"✅ 已合并加载 {len(DBC_FILENAMES)} 份DBC")
    
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (支持 Rx/Tx 混合 ASC)", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在深度解析报文 (正在匹配多DBC库)...'):
                content = uploaded_file.read()
                st.session_state.full_data = process_asc(content, db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到有效信号。请确保文件是标准的 Vector ASC 格式，且包含 Rx 或 Tx 标记，并与 DBC 匹配。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(full_data)} 个信号")

            # --- 控制面板 ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            
            with c1:
                all_sig_names = sorted(full_data.keys())
                default_keywords = ["Spd", "Current", "Volt", "Temp", "Duty"]
                default_sigs = [s for s in all_sig_names if any(k in s for k in default_keywords)]
                selected_sigs = st.multiselect(
                    "📌 信号管理 (来自合并后的数据库)", 
                    options=all_sig_names, 
                    default=default_sigs if default_sigs else all_sig_names[:2]
                )
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量轴 (Spike line)", value=True)

            if selected_sigs:
                charts_to_render = []
                for name in selected_sigs:
                    d = full_data[name]
                    x, y = d['x'], d['y']
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

                # --- 4. JS 渲染引擎 ---
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

# 侧边栏
if st.sidebar.button("♻️ 强制清除缓存并刷新"):
    st.session_state.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("HVFAN Tool v18.0 | 多DBC集成 | Rx/Tx 兼容")
