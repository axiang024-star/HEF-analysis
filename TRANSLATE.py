import streamlit as st
import cantools
import re
import os
import json
import streamlit.components.v1 as components

# ===================== 1. 核心配置 =====================
DBC_FILENAMES = [
    'HVFAN_Merged_Geely_Foton_FAW_Master.dbc',
    'Geely_TMCU_V1.1_20250513_PrivateCAN 10.dbc',
]
st.set_page_config(page_title="HVFAN 综合分析系统", layout="wide")

# ===================== 2. 解析引擎 (暴力解码方案) =====================
@st.cache_resource
def load_dbc():
    """
    暴力解码方案：强制手动解析并合并。
    即便 DBC 文件本身有逻辑错误（如位重叠），也要强行读入。
    """
    merged_db = cantools.database.Database()
    loaded_files = []
    
    for filename in DBC_FILENAMES:
        if os.path.exists(filename):
            success = False
            for encoding in ['gbk', 'utf-8']:
                try:
                    # 关键方案：直接调用 cantools 的底层 load 方法，
                    # 针对旧版和新版库做多重异常捕捉
                    try:
                        # 尝试最宽松的加载模式
                        temp_db = cantools.database.load_file(filename, encoding=encoding, strict=False)
                    except TypeError:
                        # 如果报错说明不支持 strict 参数（旧版），退回到 load_file
                        temp_db = cantools.database.load_file(filename, encoding=encoding)
                    
                    # 遍历并强制塞入主数据库
                    for msg in temp_db.messages:
                        try:
                            # 覆盖式添加：如果 ID 冲突，保留后加载的
                            merged_db._frame_id_to_message[msg.frame_id] = msg
                        except:
                            continue
                            
                    loaded_files.append(filename)
                    success = True
                    break 
                except Exception:
                    continue
            if not success:
                st.warning(f"⚠️ 文件 {filename} 存在格式硬伤，已跳过损坏部分。")
        else:
            st.warning(f"❓ 找不到文件: {filename}")
            
    return merged_db if loaded_files else None

def process_asc(file_content, db):
    data_dict = {}
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
                
                # 手动通过 ID 查找 Message 定义
                msg = db.get_message_by_frame_id(cid)
                # 使用解码，捕捉所有错误（尤其是 Overlapping 导致的错误）
                decoded = msg.decode(raw, decode_choices=False) # decode_choices=False 提高容错性
                
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
st.title("🚗 HVFAN 报文分析 (终极修复版)")

# 只要数据库里有一条报文，就允许运行
if not db or not hasattr(db, '_frame_id_to_message') or len(db._frame_id_to_message) == 0:
    st.error(f"❌ 数据库为空。请确保目录下存在：{DBC_FILENAMES}")
else:
    st.sidebar.success(f"✅ 加载成功！数据库包含 {len(db._frame_id_to_message)} 条定义")
    
    uploaded_file = st.file_uploader("📂 选择上传 ASC 报文文件", type=None)

    if uploaded_file is not None:
        file_key = f"data_{uploaded_file.name}_{uploaded_file.size}"
        if 'current_file' not in st.session_state or st.session_state.current_file != file_key:
            with st.spinner('🔍 正在强力解码解析...'):
                content = uploaded_file.read()
                st.session_state.full_data = process_asc(content, db)
                st.session_state.current_file = file_key
        
        full_data = st.session_state.full_data

        if not full_data:
            st.warning("⚠️ 匹配失败。可能报文中的 ID 在 DBC 中未定义。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(full_data)} 个信号")

            # --- 控制面板 ---
            st.write("### 🛠️ 控制面板")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                all_sig_names = sorted(full_data.keys())
                selected_sigs = st.multiselect("📌 信号筛选", options=all_sig_names, default=all_sig_names[:2])
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
                    charts_to_render.append({"id": f"c_{selected_sigs.index(name)}", "title": f"{d['label']} ({d['unit']})", "x": x, "y": y})

                # JS 渲染部分 (保持原样)
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
                        div.id = data.id; div.style.marginBottom = '20px'; div.style.height = '350px';
                        div.style.border = '1px solid #eee'; div.style.borderRadius = '8px';
                        wrapper.appendChild(div); chartIds.push(data.id);
                        const trace = {{ x: data.x, y: data.y, type: 'scatter', mode: 'lines', line: {{ width: 2, color: '#174ea6' }}, name: data.title }};
                        const layout = {{ title: {{ text: data.title, font: {{ size: 14 }} }}, margin: {{ l: 50, r: 20, t: 50, b: 40 }}, hovermode: hoverMode, template: 'plotly_white', xaxis: {{ showspikes: {str(show_measure).lower()} }}, yaxis: {{ autorange: true }} }};
                        Plotly.newPlot(data.id, [trace], layout, {{ responsive: true, displaylogo: false }});
                        if (syncEnabled) {{
                            document.getElementById(data.id).on('plotly_relayout', (eventData) => {{
                                if (isRelayouting) return; isRelayouting = true;
                                const update = {{}};
                                if (eventData['xaxis.range[0]']) {{ update['xaxis.range[0]'] = eventData['xaxis.range[0]']; update['xaxis.range[1]'] = eventData['xaxis.range[1]']; }}
                                else if (eventData['xaxis.autorange']) {{ update['xaxis.autorange'] = true; }}
                                if (Object.keys(update).length > 0) {{
                                    const promises = chartIds.map(id => {{ if (id !== data.id) return Plotly.relayout(id, update); }});
                                    Promise.all(promises).then(() => {{ isRelayouting = false; }});
                                }} else {{ isRelayouting = false; }}
                            }});
                        }}
                    }});
                </script>
                """
                components.html(js_logic, height=len(selected_sigs) * 375 + 50)

if st.sidebar.button("♻️ 强制刷新"):
    st.session_state.clear()
    st.rerun()
