import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
# 在此列表中添加所有需要加载的 DBC 文件名
DBC_FILENAMES = [
    'HVFAN_Merged_Geely_Foton_FAW_Master.dbc',
    'Geely_TMCU_V1.1_20250513_PrivateCAN 10.dbc',
]
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (兼容性合并加载) =====================
@st.cache_resource
def load_dbc():
    """
    针对旧版 cantools 的兼容性加载方案：
    手动读取内容并合并消息，绕过 strict 参数限制和信号重叠报错。
    """
    merged_db = cantools.database.Database()
    loaded_files = []
    
    for filename in DBC_FILENAMES:
        if os.path.exists(filename):
            success = False
            # 尝试不同编码读取文件内容
            for encoding in ['gbk', 'utf-8']:
                try:
                    with open(filename, 'r', encoding=encoding, errors='ignore') as f:
                        content = f.read()
                    
                    # 1. 先将字符串解析为临时数据库对象
                    tmp_db = cantools.database.load_string(content)
                    
                    # 2. 将临时库中的所有消息手动添加到合并数据库中
                    # 这种方式在旧版 cantools 中通常不会因为信号重叠而中断整个流程
                    for msg in tmp_db.messages:
                        try:
                            # 如果主库中已存在同名 ID，此处会跳过以防止冲突报错
                            merged_db.add_message(msg)
                        except:
                            continue
                    
                    loaded_files.append(filename)
                    success = True
                    break 
                except Exception:
                    continue
            if not success:
                st.warning(f"⚠️ 文件 {filename} 解析失败，请检查文件是否损坏。")
        else:
            st.warning(f"❓ 找不到文件: {filename}")
            
    return merged_db if loaded_files else None

def process_asc(file_content, db):
    data_dict = {}
    
    # 正则表达式：兼容 Rx/Tx 标记
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

# ===================== 3. UI 交互逻辑 =====================
db = load_dbc()
st.title("🚗 HVFAN 报文分析 (兼容合并版)")

if not db or len(db.messages) == 0:
    st.error(f"❌ 无法加载任何 DBC 文件。请确认文件名及路径：{DBC_FILENAMES}")
else:
    st.sidebar.success(f"✅ 已合并加载库，包含 {len(db.messages)} 条报文定义")
    
    uploaded_file = st.file_uploader("📂 上传 ASC 报文文件", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在多库匹配解析...'):
                content = uploaded_file.read()
                st.session_state.full_data = process_asc(content, db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 未能解析到有效信号。请检查 ASC 内容与 DBC 是否匹配。")
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
                    "📌 信号管理", 
                    options=all_sig_names, 
                    default=default_sigs if default_sigs else all_sig_names[:2]
                )
            with c2:
                sync_on = st.toggle("🔗 开启同步缩放", value=True)
            with c3:
                show_measure = st.toggle("📏 开启测量辅助线", value=True)

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
                        "x": x, "y": y
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
st.sidebar.caption("HVFAN Tool v18.2 | 深度兼容模式")
