import streamlit as st
import cantools
import plotly.graph_objects as go
import re
import os
import io

# ===================== 配置区域 =====================
DBC_FILENAME = 'HVFAN_CANMatrix_20241015_FAW_HVIL.dbc'

st.set_page_config(page_title="HVFAN 在线分析系统", layout="wide")

st.title("🚗 HVFAN 报文自动化分析 (移动优化版)")
st.info("提示：若手机端文件显示灰色，请确保点击“浏览”并从手机文件管理器中选择。")

# ===================== 解析逻辑 =====================
@st.cache_resource
def load_dbc():
    if os.path.exists(DBC_FILENAME):
        return cantools.database.load_file(DBC_FILENAME, encoding='gbk')
    return None

def process_asc(file_content, db):
    data_dict = {}
    # 增强型正则表达式
    frame_re = re.compile(r'\s*(?P<time>\d+\.\d+)\s+(?P<channel>\d+)\s+(?P<id>[0-9A-Fa-f]+)x?\s+Rx\s+d\s+(?P<dlc>\d+)\s+(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)')
    
    try:
        # 兼容处理：手机上传有时会改变编码
        text_data = file_content.decode('utf-8', errors='ignore')
    except:
        text_data = file_content.decode('gbk', errors='ignore')
        
    lines = text_data.split('\n')
    
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
    st.error(f"❌ 错误：未在服务器根目录找到 {DBC_FILENAME}。请检查 GitHub 仓库。")
else:
    # 修改点：取消 type=['asc'] 限制，防止手机端变灰
    uploaded_file = st.file_uploader("📂 选择并上传报文文件 (支持 .asc, .txt)", type=None)

    if uploaded_file is not None:
        with st.spinner('🔍 正在深度解析报文数据...'):
            file_bytes = uploaded_file.read()
            data_dict = process_asc(file_bytes, db)

        if not data_dict:
            st.warning("⚠️ 未识别到有效信号，请检查文件格式是否为标准的 Vector ASC 或 DBC 是否匹配。")
        else:
            st.success(f"✅ 解析成功！共识别出 {len(data_dict)} 个信号")

            # 2. 控制面板
            col1, col2 = st.columns(2)
            with col1:
                sync_on = st.toggle("🔗 开启所有图表同步缩放", value=False)
            with col2:
                show_measure = st.toggle("📏 显示测量辅助线", value=True)
            
            # 3. 渲染图表
            all_sigs = sorted(data_dict.keys())
            search_query = st.text_input("🔍 输入关键字筛选信号 (例如: Current)")
            
            for name in all_sigs:
                if search_query.lower() not in name.lower():
                    continue
                
                d = data_dict[name]
                s_label = name.split('::')[1]
                unit = d['unit']
                
                # 动态抽稀逻辑，保持手机端流畅
                x, y = d['x'], d['y']
                if len(x) > 12000:
                    step = len(x) // 10000
                    x, y = x[::step], y[::step]

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=x, y=y, 
                    name=s_label, 
                    line=dict(width=1.5),
                    hovertemplate='%{y:.2f} ' + unit + '<extra></extra>'
                ))
                
                # 核心功能：添加测量轴和同步逻辑
                fig.update_layout(
                    title=dict(text=f"信号: {s_label} ({unit})", font=dict(size=14)),
                    height=350,
                    margin=dict(l=10, r=10, t=50, b=10),
                    template="plotly_white",
                    # x unified 模式在手机上点击一次即可看所有曲线在该点的值
                    hovermode="x unified" if show_measure else "closest",
                    xaxis=dict(
                        showgrid=True,
                        showspikes=show_measure, # 开启测量辅助轴
                        spikethickness=1,
                        spikedash="dot",
                        spikemode="across",
                        spikesnap="cursor"
                    ),
                    yaxis=dict(showgrid=True)
                )
                
                if sync_on:
                    fig.update_xaxes(matches='x')

                st.plotly_chart(fig, use_container_width=True, config={'displaylogo': False})

    st.divider()
    st.caption("HVFAN Tool v15.0 | 适配移动端触控 | 测量轴功能已激活")
