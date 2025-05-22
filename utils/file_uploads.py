import os
from flask import current_app, flash
from werkzeug.utils import secure_filename

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

def save_uploaded_file(file_storage):
    """
    Saves the uploaded file if valid, returns filename or None.
    """
    if file_storage and allowed_file(file_storage.filename):
        filename = secure_filename(file_storage.filename)

        upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
        os.makedirs(upload_folder, exist_ok=True)

        file_path = os.path.join(upload_folder, filename)

        # Optional: Check for filename collisions and rename (if you want)
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(file_path):
            filename = f"{base}_{counter}{ext}"
            file_path = os.path.join(upload_folder, filename)
            counter += 1

        file_storage.save(file_path)
        return filename
    else:
        flash("Invalid file type. Allowed types: " + ", ".join(current_app.config['ALLOWED_EXTENSIONS']))
        return None
