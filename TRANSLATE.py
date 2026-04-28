import streamlit as st
import cantools
import plotly.graph_objects as go
import re
import os
import io

# ===================== 配置区域 =====================
# 将 DBC 文件放在脚本同级目录下
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

# 页面基础配置
st.set_page_config(page_title="HVFAN 在线分析系统", layout="wide")

st.title("🚗 HVFAN 报文自动化分析 (手机/PC 通用版)")
st.info("只需上传 .asc 文件，即可自动基于内置 DBC 解析信号波形。")

# ===================== 解析逻辑 =====================
@st.cache_resource # 缓存 DBC 加载，提高性能
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        return cantools.database.load_file(DBC_FILENAME, encoding='gbk')
    return None

def process_asc(file_content, db):
    data_dict = {}
    # 正则表达式适配 ASC 格式
    frame_re = re.compile(r'\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)')
    
    # 模拟文件读取
    lines = file_content.decode('utf-8', errors='ignore').split('\n')
    
    for line in lines:
        m = frame_re.match(line)
        if m:
            t, cid = float(m.group('time')), int(m.group('id'), 16)
            raw = bytearray.fromhex(m.group('data').replace(' ', ''))
            try:
                msg = db.get_message_by_frame_id(cid)
                decoded = msg.decode(raw)
                for s_n, s_v in decoded.items():
                    full_n = f"{msg.name}::{s_n}"
                    if full_n not in data_dict:
                        data_dict[full_n] = {'x': [], 'y': [], 'unit': msg.get_signal_by_name(s_n).unit or ""}
                    data_dict[full_n]['x'].append(t)
                    data_dict[full_n]['y'].append(s_v)
            except:
                continue
    return data_dict

# ===================== Web 界面交互 =====================
db = load_dbc()

if not db:
    st.error(f"❌ 错误：未在服务器找到 {DBC_FILENAME} 文件。")
else:
    # 1. 文件上传入口
    uploaded_file = st.file_uploader("📂 选择并上传 ASC 文件", type=['asc'])

    if uploaded_file is not None:
        with st.spinner('🔍 正在解析 800V 电机高频数据，请稍候...'):
            # 读取上传的文件内容
            file_bytes = uploaded_file.read()
            data_dict = process_asc(file_bytes, db)

        if not data_dict:
            st.warning("⚠️ 该报文未匹配到 DBC 中的信号。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(data_dict)} 个信号")

            # 2. 控制面板
            col1, col2 = st.columns(2)
            with col1:
                sync_on = st.toggle("同步缩放 (所有图表联动)", value=False)
            
            # 3. 渲染图表
            all_sigs = sorted(data_dict.keys())
            
            # 手机端适配：如果信号太多，提供搜索筛选
            search_query = st.text_input("🔍 筛选信号名称 (如: Spd, Volt)")
            
            for name in all_sigs:
                if search_query.lower() not in name.lower():
                    continue
                
                d = data_dict[name]
                s_label = name.split('::')[1]
                unit = d['unit']
                
                # 数据抽稀：手机浏览器内存有限，超过1万点自动抽稀
                x, y = d['x'], d['y']
                if len(x) > 10000:
                    step = len(x) // 10000
                    x, y = x[::step], y[::step]

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=x, y=y, name=s_label, line=dict(width=1.5)))
                
                # 针对同步缩放的逻辑（Streamlit 自带联动支持）
                fig.update_layout(
                    title=f"信号: {s_label} ({unit})",
                    height=300,
                    margin=dict(l=10, r=10, t=40, b=10),
                    template="plotly_white",
                    hovermode="x unified",
                    xaxis=dict(showgrid=True)
                )
                
                # 如果开启同步，所有图表共用一个 X 轴组
                if sync_on:
                    fig.update_xaxes(matches='x')

                st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.caption("Powered by Streamlit | Automotive ECU Data Analytics")