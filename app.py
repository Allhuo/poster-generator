from flask import Flask, request, send_file, jsonify, render_template
from flask_cors import CORS
import pandas as pd
from PIL import Image
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
from qrcode.image.styles.colormasks import RadialGradiantColorMask
import os
from datetime import datetime
import zipfile
import io
from threading import Lock
import logging
import time
import threading


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

progress = {}
progress_lock = Lock()

# Set up logging
logging.basicConfig(level=logging.DEBUG)


@app.route('/')
def index():
    app.logger.debug('Rendering index.html')
    return render_template('index.html')


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
    else:
        app.logger.error('No UID input provided')
        return jsonify({'error': '没有提供UID输入'}), 400

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

    # Generate QR codes and composite them onto the poster
    for index, uid in enumerate(uids):
        try:
            # Generate URL
            url = f"https://h5.jojo.kids/act2/landing.html?linkId=9533042&channel=refer_poster_all_NONE-{uid}"

            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)

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

            # Resize if necessary
            desired_size = (150, 150)
            if img_qr.size != desired_size:
                img_qr = img_qr.resize(desired_size, Image.Resampling.LANCZOS)

            # Open background poster
            img_poster = poster.copy()

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

            # Update progress for each poster generated
            with progress_lock:
                progress[job_id]['progress'] = (index + 1) / len(uids)
                progress[job_id]['status'] = f'生成海报中 ({index + 1}/{len(uids)})'
                app.logger.debug('Progress updated for job ID %s: %s', job_id, progress[job_id])
        except Exception as e:
            app.logger.error('Error generating poster: %s', e)
            return jsonify({'error': '生成海报时出错'}), 500

    # Package the posters into a ZIP file
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for image_file in os.listdir(output_folder):
            image_path = os.path.join(output_folder, image_file)
            with open(image_path, 'rb') as f:
                file_data = f.read()
            zip_file.writestr(image_file, file_data)

    # Move to the beginning of the BytesIO object
    zip_buffer.seek(0)

    # Finalize progress
    with progress_lock:
        progress[job_id]['status'] = '完成'
        progress[job_id]['progress'] = 1
        app.logger.debug('Progress finalized for job ID %s: %s', job_id, progress[job_id])

    # Send the ZIP file
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{job_id}_posters.zip'
    )


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

def cleanup_thread(job_id):
    app.logger.debug('Cleaning up for job ID: %s', job_id)

    # Wait for a minute before cleaning up
    time.sleep(60)

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