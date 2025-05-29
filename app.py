import os
import pandas as pd
import logging
import traceback
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import safe_join

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,  # 改为 DEBUG 级别以获取更多信息
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB 限制
ALLOWED_EXTENSIONS = {'.xlsx', '.csv'}

# 确保目录存在
UPLOAD_FOLDER = os.path.abspath('uploads')
EXPORT_FOLDER = os.path.abspath('exports')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)

logger.info(f"上传目录: {UPLOAD_FOLDER}")
logger.info(f"导出目录: {EXPORT_FOLDER}")

def allowed_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def process_file(file_path):
    try:
        logger.info(f"开始处理文件: {file_path}")
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None, None, "文件不存在"
            
        # 检查文件大小
        file_size = os.path.getsize(file_path)
        logger.info(f"文件大小: {file_size} 字节")
        
        # 读取文件
        if file_path.endswith('.xlsx'):
            try:
                df = pd.read_excel(file_path)
                logger.info(f"成功读取Excel文件，列名: {list(df.columns)}")
            except Exception as e:
                logger.error(f"读取Excel文件失败: {str(e)}\n{traceback.format_exc()}")
                return None, None, "读取Excel文件失败，请检查文件格式"
        else:
            encodings = ['utf-8', 'gbk', 'gb18030']
            for encoding in encodings:
                try:
                    df = pd.read_csv(file_path, encoding=encoding)
                    logger.info(f"成功使用 {encoding} 编码读取CSV文件，列名: {list(df.columns)}")
                    break
                except UnicodeDecodeError:
                    continue
            else:
                logger.error("无法读取文件，编码问题")
                return None, None, "无法读取文件，请检查文件编码"

        # 检查必要的列
        required_columns = ['商家实收金额(元)', '快递单号', '售后状态', '订单状态']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            logger.error(f"文件缺少必要的列: {missing_columns}")
            return None, None, f"文件缺少必要的列: {', '.join(missing_columns)}"

        # 数据处理
        try:
            # 转换金额列
            df['商家实收金额(元)'] = pd.to_numeric(df['商家实收金额(元)'], errors='coerce').fillna(0)
            logger.info("成功转换金额列")
            
            # 处理售后状态
            df['售后状态'] = df['售后状态'].fillna('')
            df['是否无售后'] = df['售后状态'].str.contains('无售后或售后取消', na=False)
            logger.info("成功处理售后状态")
            
            # 标记有效订单
            df['订单有效'] = ~(
                df['订单状态'].str.contains('退款', na=False) | 
                df['订单状态'].str.contains('已取消', na=False) |
                df['订单状态'].str.contains('待付款', na=False)
            )
            logger.info("成功标记有效订单")
            
            # 标记发货状态
            df['发货状态'] = '其他'
            df.loc[df['订单状态'].str.contains('已发货', na=False), '发货状态'] = '已发货'
            df.loc[
                (df['订单状态'].str.contains('未发货|待发货', na=False)) &
                ~df['订单状态'].str.contains('退款', na=False),
                '发货状态'
            ] = '未发货'
            logger.info("成功标记发货状态")

            # 标记已收货状态
            df['收货状态'] = df['订单状态'].str.contains('已收货', na=False)
            logger.info("成功标记收货状态")
            
            # 筛选有效订单
            valid_orders = df[df['订单有效']]
            logger.info(f"有效订单数量: {len(valid_orders)}")
            
            # 统计数据
            stats = {
                '实际销售订单数': len(valid_orders),
                '实际销售金额': float(valid_orders['商家实收金额(元)'].sum()),
                '已发货订单数': len(valid_orders[valid_orders['发货状态'] == '已发货']),
                '已发货订单金额': float(valid_orders[valid_orders['发货状态'] == '已发货']['商家实收金额(元)'].sum()),
                '未发货订单数': len(valid_orders[valid_orders['发货状态'] == '未发货']),
                '未发货订单金额': float(valid_orders[valid_orders['发货状态'] == '未发货']['商家实收金额(元)'].sum()),
                '无售后订单数': len(valid_orders[valid_orders['是否无售后']]),
                '无售后订单金额': float(valid_orders[valid_orders['是否无售后']]['商家实收金额(元)'].sum()),
                '退款订单数': len(df[df['订单状态'].str.contains('退款成功', na=False)]),
                '退款订单金额': float(df[df['订单状态'].str.contains('退款成功', na=False)]['商家实收金额(元)'].sum()),
                '已收货订单数': len(valid_orders[valid_orders['收货状态']]),
                '已收货订单金额': float(valid_orders[valid_orders['收货状态']]['商家实收金额(元)'].sum())
            }
            logger.info(f"统计数据: {stats}")
            
            # 导出数据
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            export_filename = f'processed_orders_{timestamp}.xlsx'
            export_path = os.path.join(EXPORT_FOLDER, export_filename)
            
            # 准备导出数据
            export_df = valid_orders.copy()
            summary_data = {
                '订单状态': ['汇总'],
                '商家实收金额(元)': [stats['实际销售金额']],
                '订单数量': [stats['实际销售订单数']],
                '已发货订单数': [stats['已发货订单数']],
                '未发货订单数': [stats['未发货订单数']],
                '无售后订单数': [stats['无售后订单数']]
            }
            
            # 添加汇总行
            export_df = pd.concat([export_df, pd.DataFrame(summary_data)], ignore_index=True)
            
            # 导出到Excel
            try:
                export_df.to_excel(export_path, index=False, engine='openpyxl')
                logger.info(f"文件导出成功: {export_filename}")
            except Exception as e:
                logger.error(f"导出错误: {str(e)}\n{traceback.format_exc()}")
                return None, None, "导出Excel文件失败"
                
            return stats, export_filename, None
            
        except Exception as e:
            logger.error(f"数据处理错误: {str(e)}\n{traceback.format_exc()}")
            return None, None, f"数据处理时发生错误: {str(e)}"
            
    except Exception as e:
        logger.error(f"处理错误: {str(e)}\n{traceback.format_exc()}")
        return None, None, f"处理文件时发生错误: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            logger.warning("没有上传文件")
            return jsonify({'error': '没有上传文件'})
        
        file = request.files['file']
        if file.filename == '':
            logger.warning("未选择文件")
            return jsonify({'error': '未选择文件'})
        
        if not allowed_file(file.filename):
            logger.warning(f"不支持的文件类型: {file.filename}")
            return jsonify({'error': '请上传Excel文件(.xlsx)或CSV文件(.csv)'})
        
        try:
            file_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(file_path)
            logger.info(f"文件已保存: {file_path}")
            
            stats, export_filename, error = process_file(file_path)
            
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"临时文件已删除: {file_path}")
            
            if error:
                return jsonify({'error': error})
            
            return jsonify({
                'stats': stats,
                'export_filename': export_filename
            })
        except Exception as e:
            logger.error(f"上传处理错误: {str(e)}\n{traceback.format_exc()}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({'error': f'处理文件时发生错误: {str(e)}'})
    except Exception as e:
        logger.error(f"上传路由错误: {str(e)}\n{traceback.format_exc()}")
        return jsonify({'error': f'服务器错误: {str(e)}'})

@app.route('/download/<filename>')
def download_file(filename):
    try:
        file_path = os.path.join(EXPORT_FOLDER, filename)
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return jsonify({'error': '文件不存在'})
        
        logger.info(f"开始下载文件: {filename}")
        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"文件下载失败: {str(e)}\n{traceback.format_exc()}")
        return jsonify({'error': '文件下载失败'})

if __name__ == '__main__':
    app.run(debug=True, port=5000) 