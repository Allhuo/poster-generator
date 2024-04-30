import json
import logging
import os
import threading
import time
import zipfile
from datetime import datetime
from threading import Lock

import pandas as pd
import pytz
import qrcode
from PIL import Image
from flask import Flask, request, send_file, jsonify, render_template, after_this_request
from flask import g
from flask_cors import CORS
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import RadialGradiantColorMask
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer


# Set timezone
def get_timezone():
    return pytz.timezone('Asia/Shanghai')


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

progress = {}
progress_lock = Lock()

# Set up logging
logging.basicConfig(level=logging.DEBUG)

app.before_request(lambda: setattr(g, 'tz', get_timezone()))


@app.route('/')
def index():
    app.logger.debug('Rendering index.html')
    return render_template('index.html')


@app.route('/logs')
def logs():
    # 读取生成日志数据
    log_data = read_log_data()
    return render_template('logs.html', logs=log_data)


@app.route('/upload', methods=['POST'])
def upload_files():
    job_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    app.logger.debug('Job ID created: %s', job_id)

    # Initialize progress
    with progress_lock:
        progress[job_id] = {'status': '等待上传文件', 'progress': 0}
        app.logger.debug('Progress initialized for job ID: %s', job_id)

    # Return job_id to frontend
    return jsonify({'jobId': job_id})


@app.route('/download/<job_id>')
def download_zip(job_id):
    output_folder = f"output_{job_id}"
    zip_filename = f'{job_id}_posters.zip'
    zip_path = os.path.join(output_folder, zip_filename)

    if not os.path.exists(zip_path):
        return jsonify({'error': '文件不存在'}), 404

    return send_file(
        zip_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=zip_filename
    )


@app.route('/process/<job_id>', methods=['POST'])
def process_files(job_id):
    app.logger.debug('Processing files for job ID: %s', job_id)

    # Update progress
    with progress_lock:
        progress[job_id]['status'] = '文件上传完成,开始处理'
        app.logger.debug('Progress updated for job ID %s: %s', job_id, progress[job_id])

    # Check input method
    if 'uid-file' in request.files:
        uid_file = request.files['uid-file']
        if uid_file.filename == '':
            app.logger.error('No UID file selected')
            return jsonify({'error': '没有选择UID文件'}), 400
        try:
            df = pd.read_excel(uid_file)
            uids = df['UID'].tolist()
        except Exception as e:
            app.logger.error('Error reading UID file: %s', e)
            return jsonify({'error': '读取UID文件时出错'}), 400
    elif 'uid-text' in request.form:
        uid_text = request.form['uid-text']
        if uid_text.strip() == '':
            app.logger.error('No UIDs provided')
            return jsonify({'error': '没有提供UID'}), 400

        uids = [uid.strip() for uid in uid_text.split('\n') if uid.strip()]
        print("接收到的UIDs:", uids)  # 打印接收到的UIDs
    else:
        app.logger.error('No UID input provided')
        return jsonify({'error': '没有提供UID输入'}), 400

    url_prefix_method = request.form.get('urlPrefixMethod', 'default')
    app.logger.debug(f'URL prefix method: {url_prefix_method}')
    custom_url_prefix = request.form.get('custom-url-prefix', '')
    app.logger.debug(f'Custom URL prefix: {custom_url_prefix}')

    # Modify URL generation based on user choice
    for index, uid in enumerate(uids):
        if url_prefix_method == 'custom' and custom_url_prefix:
            url = f"{custom_url_prefix}-{uid}"
        else:
            url = f"https://h5.jojo.kids/act2/landing.html?linkId=9533042&channel=refer_poster_all_NONE-{uid}"
    app.logger.debug(f'Generated URL for UID {uid}: {url}')

    # Check poster method
    if 'poster-file' in request.files:
        poster_file = request.files['poster-file']
        if poster_file.filename == '':
            app.logger.error('No poster file selected')
            return jsonify({'error': '没有选择海报文件'}), 400

        try:
            poster = Image.open(poster_file.stream)
        except IOError as e:
            app.logger.error('Error reading poster file: %s', e)
            return jsonify({'error': '无法读取海报文件'}), 400
    else:
        try:
            poster = Image.open('static/poster.png')
        except IOError as e:
            app.logger.error('Error reading default poster file: %s', e)
            return jsonify({'error': '无法读取默认海报文件'}), 500

    # Create output directory
    output_folder = f"output_{job_id}"
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 读取 UID 和短链接的对应关系
    df_urls = pd.read_csv('./static/uid_shorturls.csv')
    df_urls.columns = ['uid', 'shortUrl']
    df_urls = df_urls.set_index('uid')

    # 存储 UID 和短链接的字典
    uid_shorturl_dict = {}

    # Generate QR codes and composite them onto the poster
    for index, uid in enumerate(uids):
        try:
            # 获取对应的短链接
            short_url = df_urls.loc[int(uid), 'shortUrl']
            app.logger.debug(f'Short URL: {short_url}')  # Add this line
            uid_shorturl_dict[uid] = short_url  # 改为'shortUrl'

            # Generate URL
            app.logger.debug(f'Generated URL: {url}')

            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            app.logger.debug('QR code created')

            # Create QR code image with specified color and rounded modules
            img_qr = qr.make_image(
                image_factory=StyledPilImage,
                module_drawer=RoundedModuleDrawer(),
                color_mask=RadialGradiantColorMask(
                    center_color=(90, 142, 54),
                    edge_color=(90, 142, 54),
                    back_color=(255, 255, 255)
                )
            )
            app.logger.debug('QR code image created')

            # Resize if necessary
            desired_size = (150, 150)
            if img_qr.size != desired_size:
                img_qr = img_qr.resize(desired_size, Image.Resampling.LANCZOS)
            app.logger.debug('QR code image resized')

            # Open background poster
            img_poster = poster.copy()
            app.logger.debug('Background poster opened')

            # Calculate QR code position
            position = (563, 1052)

            # Paste QR code onto the poster
            if img_qr.mode == 'RGBA':
                mask = img_qr.split()[3]
                img_poster.paste(img_qr, position, mask)
            else:
                img_poster.paste(img_qr, position)

            # Save the composite poster image
            output_path = os.path.join(output_folder, f"{uid}.png")
            img_poster.save(output_path)
            app.logger.debug(f'Composite poster image saved at {output_path}')

            # Update progress for each poster generated
            with progress_lock:
                progress[job_id]['progress'] = (index + 1) / len(uids)
                progress[job_id]['status'] = f'生成海报中 ({index + 1}/{len(uids)})'
                app.logger.debug('Progress updated for job ID %s: %s', job_id, progress[job_id])
        except Exception as e:
            app.logger.error('Error generating poster: %s', e)
            return jsonify({'error': '生成海报时出错'}), 500

    # Package the posters into a ZIP file
    zip_filename = f'{job_id}_posters.zip'
    zip_path = os.path.join(output_folder, zip_filename)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for image_file in os.listdir(output_folder):
            if image_file.endswith('.png'):  # 只打包PNG图片文件
                image_path = os.path.join(output_folder, image_file)
                zip_file.write(image_path, image_file)

    # Finalize progress
    with progress_lock:
        progress[job_id]['status'] = '完成'
        progress[job_id]['progress'] = 1
        app.logger.debug('Progress finalized for job ID %s: %s', job_id, progress[job_id])

    @after_this_request
    def write_log(response):
        try:
            log_data = {
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'uid_count': len(uids),
                'uids': ', '.join(uids),
                'result': '成功'
            }
            write_log_data(log_data)
        except Exception as e:
            app.logger.error('Error writing log data: %s', e)
        return response

    app.logger.debug(f'UID-ShortURL Dictionary: {uid_shorturl_dict}')

    # Return the download link and short URLs
    return jsonify({'zip_url': f'/download/{job_id}', 'uid_shorturls': uid_shorturl_dict})


@app.route('/progress/<job_id>')
def get_progress(job_id):
    app.logger.debug('Getting progress for job ID: %s', job_id)
    with progress_lock:
        progress_data = progress.get(job_id, {'status': '未知的任务', 'progress': 0})
        app.logger.debug('Progress data for job ID %s: %s', job_id, progress_data)
        return jsonify(progress_data)


@app.route('/cleanup/<job_id>', methods=['POST'])
def cleanup(job_id):
    threading.Thread(target=cleanup_thread, args=(job_id,)).start()
    return jsonify({'message': 'Cleanup started'})


def read_log_data():
    try:
        with open('logs/log_data.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.decoder.JSONDecodeError as e:
        app.logger.error('Error decoding JSON data: %s', e)
        return []


def write_log_data(log_data):
    try:
        logs = read_log_data()
        logs.append(log_data)
        with open('logs/log_data.json', 'w') as f:
            json.dump(logs, f, indent=2)  # 使用 indent 参数增加可读性
    except Exception as e:
        app.logger.error('Error writing log data: %s', e)


def cleanup_thread(job_id):
    app.logger.debug('Cleaning up for job ID: %s', job_id)

    # Wait for a minute before cleaning up
    time.sleep(120)

    # Remove the job_id from progress
    with progress_lock:
        progress.pop(job_id, None)
    app.logger.debug('Job ID removed from progress: %s', job_id)

    # Cleanup temporary files
    try:
        output_folder = f"output_{job_id}"
        if os.path.exists(output_folder):
            for image_file in os.listdir(output_folder):
                os.remove(os.path.join(output_folder, image_file))
            os.rmdir(output_folder)
            app.logger.debug('Temporary files cleaned up for job ID: %s', job_id)
    except Exception as e:
        app.logger.error('Error cleaning up temporary files: %s', str(e))


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=7070, debug=True)
