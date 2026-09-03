from pathlib import Path
import os
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("EDUSPHERE_SECRET_KEY", "change-this-in-production")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("EDUSPHERE_SECURE_COOKIES", "0") == "1",
)
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def get_database_connection():
    return mysql.connector.connect(
        host=os.getenv("EDUSPHERE_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("EDUSPHERE_DB_PORT", "3306")),
        user=os.getenv("EDUSPHERE_DB_USER", "root"),
        password=os.getenv("EDUSPHERE_DB_PASSWORD", ""),
        database=os.getenv("EDUSPHERE_DB_NAME", "edusphere"),
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Authentication required"}), 401
        return view(*args, **kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({"error": "Admin access required"}), 403
        return view(*args, **kwargs)
    return wrapped_view

@app.route('/')
def home():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/api/health')
def health():
    return {"message": "EduSphere AI Backend Running"}


@app.post('/api/login')
def login():
    credentials = request.get_json(silent=True) or {}
    email = credentials.get('email', '').strip().lower()
    password = credentials.get('password', '')
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    connection = None
    try:
        connection = get_database_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, email, password_hash, full_name, department, role "
            "FROM users WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()
    except mysql.connector.Error:
        return jsonify({"error": "Database is unavailable"}), 503
    finally:
        if connection and connection.is_connected():
            connection.close()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({"error": "Invalid email or password"}), 401

    session.clear()
    session['user_id'] = user['id']
    session['role'] = user['role']
    return jsonify({
        "user": {
            "id": user['id'],
            "email": user['email'],
            "full_name": user['full_name'],
            "department": user['department'],
            "role": user['role'],
        }
    })


@app.post('/api/logout')
@login_required
def logout():
    session.clear()
    return jsonify({"message": "Signed out successfully"})


@app.get('/api/dashboard')
@login_required
def dashboard():
    connection = None
    try:
        connection = get_database_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, email, full_name, department FROM users WHERE id = %s",
            (session['user_id'],),
        )
        user = cursor.fetchone()
        cursor.execute(
            "SELECT title, course_name, due_date, completed FROM assignments "
            "WHERE user_id = %s ORDER BY due_date LIMIT 10",
            (session['user_id'],),
        )
        assignments = cursor.fetchall()
        cursor.execute(
            "SELECT title, body, published_at FROM notices "
            "ORDER BY published_at DESC LIMIT 5"
        )
        notices = cursor.fetchall()
        cursor.execute(
            "SELECT COALESCE(ROUND(100 * SUM(attended) / NULLIF(COUNT(*), 0), 1), 0) "
            "AS attendance FROM attendance WHERE user_id = %s",
            (session['user_id'],),
        )
        attendance = cursor.fetchone()['attendance']
        cursor.execute("SELECT COALESCE(ROUND(AVG(progress), 0), 0) AS course_progress FROM courses")
        course_progress = cursor.fetchone()['course_progress']
        cursor.execute(
            "SELECT COUNT(*) AS pending_assignments FROM assignments "
            "WHERE user_id = %s AND completed = FALSE",
            (session['user_id'],),
        )
        pending_assignments = cursor.fetchone()['pending_assignments']
    except mysql.connector.Error:
        return jsonify({"error": "Database is unavailable"}), 503
    finally:
        if connection and connection.is_connected():
            connection.close()

    if not user:
        session.clear()
        return jsonify({"error": "User account no longer exists"}), 401

    return jsonify({
        "user": user,
        "assignments": assignments,
        "notices": notices,
        "stats": {
            "attendance": attendance,
            "course_progress": course_progress,
            "pending_assignments": pending_assignments,
        },
    })


@app.patch('/api/assignments/<int:assignment_id>')
@login_required
def update_assignment(assignment_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get('completed'), bool):
        return jsonify({"error": "completed must be a boolean"}), 400

    connection = None
    try:
        connection = get_database_connection()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE assignments SET completed = %s WHERE id = %s AND user_id = %s",
            (payload['completed'], assignment_id, session['user_id']),
        )
        connection.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "Assignment not found"}), 404
    except mysql.connector.Error:
        return jsonify({"error": "Database is unavailable"}), 503
    finally:
        if connection and connection.is_connected():
            connection.close()

    return jsonify({"id": assignment_id, "completed": payload['completed']})


@app.get('/api/admin/overview')
@admin_required
def admin_overview():
    connection = None
    try:
        connection = get_database_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS total_students FROM users WHERE role = 'student'")
        total_students = cursor.fetchone()['total_students']
        cursor.execute("SELECT COUNT(*) AS total_courses FROM courses")
        total_courses = cursor.fetchone()['total_courses']
        cursor.execute("SELECT COUNT(*) AS total_assignments FROM assignments")
        total_assignments = cursor.fetchone()['total_assignments']
        cursor.execute("SELECT COUNT(*) AS total_notices FROM notices")
        total_notices = cursor.fetchone()['total_notices']
    except mysql.connector.Error:
        return jsonify({"error": "Database is unavailable"}), 503
    finally:
        if connection and connection.is_connected():
            connection.close()

    return jsonify({
        "students": total_students,
        "courses": total_courses,
        "assignments": total_assignments,
        "notices": total_notices,
    })


@app.post('/api/admin/notices')
@admin_required
def create_notice():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get('title', '')).strip()
    body = str(payload.get('body', '')).strip()
    if not title or not body:
        return jsonify({"error": "Title and body are required"}), 400
    if len(title) > 180:
        return jsonify({"error": "Title must be 180 characters or fewer"}), 400

    connection = None
    try:
        connection = get_database_connection()
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO notices (title, body) VALUES (%s, %s)",
            (title, body),
        )
        connection.commit()
        notice_id = cursor.lastrowid
    except mysql.connector.Error:
        return jsonify({"error": "Database is unavailable"}), 503
    finally:
        if connection and connection.is_connected():
            connection.close()

    return jsonify({"id": notice_id, "title": title, "body": body}), 201


@app.delete('/api/admin/notices/<int:notice_id>')
@admin_required
def delete_notice(notice_id):
    connection = None
    try:
        connection = get_database_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
        connection.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "Notice not found"}), 404
    except mysql.connector.Error:
        return jsonify({"error": "Database is unavailable"}), 503
    finally:
        if connection and connection.is_connected():
            connection.close()

    return jsonify({"message": "Notice deleted"})

if __name__ == '__main__':
    app.run(debug=os.getenv("EDUSPHERE_DEBUG", "0") == "1")
