from flask import Flask, render_template, request, redirect, url_for, send_file
import sqlite3
from io import BytesIO
import os # SESSION MANAGEMENT (LOGGING IN OF ACCOUNTS)
from flask import session # LOGGING IN stores user id and role in the session:
from flask import flash # Indicates warning messages
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_very_secret_random_string' # Use a random string here
# Database setup
DATABASE = 'hunii_database.db'

def get_db_connection():
    """Connect to the SQLite database and return the connection."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON;") # Only valid students can make requests
    conn.row_factory = sqlite3.Row  # Enables column access by name
    return conn

# ----------------------------- DATABASE INITIALIZATION ----------------------------- #

def init_db():
    """Initializes all tables in the database within a single connection."""
    conn = get_db_connection()
    
    # ------------------------------ No. 1: USERS TABLE ------------------------------ #
    conn.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('student', 'instructor'))
    )''') # CHECK (role IN ('student', 'instructor')) limit role entries to student / instructor only

    # ---------------------------- No. 2: COURSE REQUEST TABLE ---------------------------- #
    conn.execute('''
    CREATE TABLE IF NOT EXISTS course_requests (
        request_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        topic_name TEXT NOT NULL,
        description TEXT NOT NULL,
        tags TEXT,  -- <--- ADDED THIS COLUMN
        status TEXT DEFAULT 'pending',
        FOREIGN KEY (student_id) REFERENCES users (user_id)
    )''')

    # ------------------------- No. 3: SCHEDULED SESSIONS TABLE ------------------------- #
    conn.execute('''
    CREATE TABLE IF NOT EXISTS scheduled_sessions (
        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        instructor_id INTEGER NOT NULL,
        date_time TEXT NOT NULL,
        zoom_link TEXT NOT NULL,
        max_slots INTEGER NOT NULL,
        current_enrollment INTEGER DEFAULT 1, -- Automatically starts at 1
        FOREIGN KEY (request_id) REFERENCES course_requests (request_id),
        FOREIGN KEY (instructor_id) REFERENCES users (user_id)
    )''')

    # ------------------------- No. 4 ENROLLMENTS TABLE ------------------------- #
    conn.execute('''
    CREATE TABLE IF NOT EXISTS enrollments (
        enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        enrolled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES scheduled_sessions (session_id),
        FOREIGN KEY (student_id) REFERENCES users (user_id)
        UNIQUE(session_id, student_id)  
    )''')

    conn.commit() # Saves all changes at once
    conn.close()  # Closes the connection after everything is done

# Ensure the database is initialized when the script runs
init_db()

# --------------------------------------- ROUTES --------------------------------------- #
# ROUTE: LANDING PAGE (index.html)
# ROUTE: LANDING PAGE
@app.route('/')
def index():
    # Optional: If they ARE logged in, skip the landing page and go to dashboard
    if 'user_id' in session:
        if session.get('role') == 'instructor':
            return redirect(url_for('instructor_dashboard'))
        return redirect(url_for('student_dashboard'))
    
    # If not logged in, show the landing page with Login/Register options
    return render_template('index.html')

# ROUTE: REGISTRATION PAGE
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                         (username, password, role))
            conn.commit()
            flash("Registration successful! Please login.")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Username already exists!")
        finally:
            conn.close()
            
    return render_template('register.html')

# ROUTE: LOGIN PAGE
@app.route('/login', methods=['GET', 'POST']) # Added GET so you can see the page
def login():
    if request.method == 'POST': # Submits username and password for checking (Login)
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', # Select the account where the username matches its password
                            (username, password)).fetchone()
        conn.close()

        if user: # Basically assigns the specific infos to its respective user (the one who currently logged in) so the system know his / her details
            session['user_id'] = user['user_id']
            session['username'] = user['username']
            session['role'] = user['role']
            
            if user['role'] == 'instructor': # This sends the user to the dashboard that matches their role
                return redirect(url_for('instructor_dashboard'))
            return redirect(url_for('student_dashboard'))
        
        flash("Invalid Credentials") # This indicates that the user fails to login (No USERNAME & PASSWORD matched)
    return render_template('login.html') # You'll need this template

# ROUTE: STUDENT DASHBOARD
@app.route('/student_dashboard')
def student_dashboard():
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('login'))

    student_id = session['user_id']
    conn = get_db_connection()
    
    # --- 1. AVAILABLE SESSIONS ---
    available_query = '''
        SELECT r.topic_name, s.session_id, s.date_time, u.username AS instructor_name,
               s.max_slots, s.current_enrollment
        FROM scheduled_sessions s
        JOIN course_requests r ON s.request_id = r.request_id
        JOIN users u ON s.instructor_id = u.user_id
        WHERE s.current_enrollment < s.max_slots
        AND s.session_id NOT IN (SELECT session_id FROM enrollments WHERE student_id = ?)
    '''
    raw_available = conn.execute(available_query, (student_id,)).fetchall()

    # 2. MY REQUESTS 
    my_requests = conn.execute('''
        SELECT request_id, topic_name, description, tags, status -- ADDED tags HERE
        FROM course_requests 
        WHERE student_id = ?
    ''', (student_id,)).fetchall()

    # 3. MY ENROLLED SESSIONS (Updated to include session_id)
    raw_confirmed = conn.execute('''
        SELECT s.session_id, r.topic_name, s.date_time, s.zoom_link, u.username AS instructor_name,
               s.max_slots, s.current_enrollment
        FROM enrollments e
        JOIN scheduled_sessions s ON e.session_id = s.session_id
        JOIN course_requests r ON s.request_id = r.request_id
        JOIN users u ON s.instructor_id = u.user_id
        WHERE e.student_id = ?
    ''', (student_id,)).fetchall()

    conn.close()

    # --- DATE FORMATTING LOGIC ---
    # Helper function to convert ISO string to "Month Day, Year - Time"
    def format_dt(rows):
        formatted_list = []
        for row in rows:
            # Convert row to dict so we can modify the date_time value
            d = dict(row)
            try:
                # Converts '2026-04-01T23:51' -> 'April 01, 2026 - 11:51 PM'
                dt_obj = datetime.fromisoformat(d['date_time'])
                d['date_time'] = dt_obj.strftime("%B %d, %Y - %I:%M %p")
            except (ValueError, TypeError):
                pass # Keep original if format is weird
            formatted_list.append(d)
        return formatted_list

    # Apply formatting
    available_sessions = format_dt(raw_available)
    my_confirmed = format_dt(raw_confirmed)

    return render_template('student_dashboard.html', 
                           sessions=available_sessions, 
                           requests=my_requests, 
                           confirmed=my_confirmed)

# ROUTE: SUBMIT REQUEST PAGE (Student Dashboard)
@app.route('/submit_request', methods=['POST'])
def submit_request():
    if 'user_id' not in session or session['role'] != 'student':
        flash("Access denied. Students only.")
        return redirect(url_for('login'))

    student_id = session['user_id'] 
    topic = request.form['topic_name']
    desc = request.form['description']
    
    # 1. GRAB THE NEW DATA FROM YOUR UPDATED HTML
    subject = request.form.get('subject')
    grade = request.form.get('grade_level')
    special_needs = request.form.getlist('special_tags') # This gets the checkbox list
    
    # 2. COMBINE THEM INTO THE 'TAGS' STRING
    # Example result: "Mathematics, Grade 10 (Junior High), Low-Data, ADHD Support"
    all_tags = f"{subject}, {grade}"
    if special_needs:
        all_tags += ", " + ", ".join(special_needs)

    conn = get_db_connection()
    # 3. ENSURE 'tags' IS IN THE INSERT STATEMENT
    conn.execute('''
        INSERT INTO course_requests (student_id, topic_name, description, tags) 
        VALUES (?, ?, ?, ?)
    ''', (student_id, topic, desc, all_tags))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('student_dashboard'))

# ROUTE: CANCEL REQUEST (Student Dashboard)
@app.route('/cancel_request/<int:request_id>', methods=['POST'])
def cancel_request(request_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('login'))

    student_id = session['user_id']
    conn = get_db_connection()
    
    # We only allow deletion if the request belongs to the student AND it's still 'pending'
    # Once it's 'accepted', it becomes a scheduled session (which needs a different 'withdraw' logic)
    conn.execute('''
        DELETE FROM course_requests 
        WHERE request_id = ? AND student_id = ? AND status = 'pending'
    ''', (request_id, student_id))
    
    conn.commit()
    conn.close()
    
    flash("Request cancelled successfully.")
    return redirect(url_for('student_dashboard'))

# ROUTE: WITHDRAW / UNENROLL SESSION (Student Dashboard)
@app.route('/withdraw_session/<int:session_id>', methods=['POST'])
def withdraw_session(session_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return redirect(url_for('login'))

    student_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Remove the student from the enrollments table
        cursor.execute('''
            DELETE FROM enrollments 
            WHERE session_id = ? AND student_id = ?
        ''', (session_id, student_id))

        # 2. Check if anything was actually deleted before updating the count
        if cursor.rowcount > 0:
            cursor.execute('''
                UPDATE scheduled_sessions 
                SET current_enrollment = current_enrollment - 1 
                WHERE session_id = ?
            ''', (session_id,))
            
            conn.commit()
            flash("You have successfully withdrawn from the session.")
        else:
            flash("You were not enrolled in this session.")

    except Exception as e:
        conn.rollback()
        flash(f"An error occurred: {e}")
    finally:
        conn.close()

    return redirect(url_for('student_dashboard'))

# ROUTE: INSTRUCTOR DASHBOARD
@app.route('/instructor_dashboard')
def instructor_dashboard():
    if 'user_id' not in session or session.get('role') != 'instructor':
        flash("Access denied. Instructors only.")
        return redirect(url_for('login'))

    instructor_id = session['user_id']
    conn = get_db_connection()
    
    # --- 1. THE ANALYTICS (NEW) ---
    # Count total pending
    total_pending = conn.execute('SELECT COUNT(*) FROM course_requests WHERE status = "pending"').fetchone()[0]
    
    # Count requests with "Support" or "ADHD" or "Low-Data" in tags
    inclusive_count = conn.execute('''
        SELECT COUNT(*) FROM course_requests 
        WHERE status = "pending" AND (tags LIKE "%Support%" OR tags LIKE "%Low-Data%" OR tags LIKE "%Special%")
    ''').fetchone()[0]

    # --- 2. THE HEATMAP (NEW) ---
    # Get counts per subject for the "Trending" section
    # We use topic_name or subject if you saved it that way
    hot_topics = conn.execute('''
        SELECT topic_name, COUNT(*) as count 
        FROM course_requests 
        WHERE status = "pending" 
        GROUP BY topic_name 
        ORDER BY count DESC LIMIT 5
    ''').fetchall()

    # --- 3. FETCH PENDING REQUESTS (Existing) ---
    query_pending = '''
        SELECT r.request_id, r.topic_name, r.description, r.tags, u.username 
        FROM course_requests r
        JOIN users u ON r.student_id = u.user_id
        WHERE r.status = 'pending'
    '''
    pending_requests = conn.execute(query_pending).fetchall()

    # --- 4. FETCH SCHEDULED SESSIONS (Existing) ---
    query_scheduled = '''
        SELECT s.session_id, r.topic_name, s.date_time, s.zoom_link, 
               s.current_enrollment, s.max_slots
        FROM scheduled_sessions s
        JOIN course_requests r ON s.request_id = r.request_id
        WHERE s.instructor_id = ?
    '''
    raw_scheduled = conn.execute(query_scheduled, (instructor_id,)).fetchall()
    conn.close()

    # Date formatting logic...
    formatted_scheduled = []
    for row in raw_scheduled:
        d = dict(row)
        try:
            dt_obj = datetime.fromisoformat(d['date_time'])
            d['date_time'] = dt_obj.strftime("%B %d, %Y - %I:%M %p")
        except: pass
        formatted_scheduled.append(d)

    return render_template('instructor_dashboard.html', 
                           requests=pending_requests, 
                           scheduled=formatted_scheduled,
                           total_pending=total_pending,
                           inclusive_count=inclusive_count,
                           hot_topics=hot_topics)

# ROUTE: PREPARATION ROUTE (ACCEPT REQUEST FORM)
@app.route('/prepare_session/<int:request_id>')
def prepare_session(request_id):
    # Gatekeeper check here...
    if 'user_id' not in session or session.get('role') != 'instructor':
        return redirect(url_for('login'))
        
    return render_template('prepare_session.html', request_id=request_id)

@app.route('/accept_request/<int:request_id>', methods=['POST'])
def accept_request(request_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return redirect(url_for('login'))

    # Collect details from the form
    instructor_id = session['user_id']
    date_time = request.form['date_time']
    zoom_link = request.form['zoom_link']
    max_slots = request.form['max_slots']

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Create the Session in Table No. 3
        # We use current_enrollment = 1 because the requester is automatically in
        cursor.execute('''
            INSERT INTO scheduled_sessions (request_id, instructor_id, date_time, zoom_link, max_slots)
            VALUES (?, ?, ?, ?, ?)
        ''', (request_id, instructor_id, date_time, zoom_link, max_slots))
        
        new_session_id = cursor.lastrowid # Get the ID of the session we just made

        # 2. Find the original student from Table No. 2
        req = conn.execute('SELECT student_id FROM course_requests WHERE request_id = ?', (request_id,)).fetchone()
        original_student_id = req['student_id']

        # 3. Auto-Enroll that student in Table No. 4
        cursor.execute('''
            INSERT INTO enrollments (session_id, student_id)
            VALUES (?, ?)
        ''', (new_session_id, original_student_id))

        # 4. Update Request Status in Table No. 2
        cursor.execute('UPDATE course_requests SET status = "accepted" WHERE request_id = ?', (request_id,))

        conn.commit()
        flash("Session scheduled and student enrolled!")
    except Exception as e: # Error Handling
        conn.rollback()
        flash(f"Error: {e}")
    finally:
        conn.close()

    return redirect(url_for('instructor_dashboard'))

# ROUTE: INSTRUCTOR WITHDRAWS / DROPS A CLASS (Instructor's Dashboard)
@app.route('/instructor_withdraw/<int:session_id>', methods=['POST'])
def instructor_withdraw(session_id):
    if 'user_id' not in session or session.get('role') != 'instructor':
        return redirect(url_for('login'))

    instructor_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Fetch session info first to check enrollment count and request_id
        session_info = cursor.execute('''
            SELECT request_id, current_enrollment FROM scheduled_sessions 
            WHERE session_id = ? AND instructor_id = ?
        ''', (session_id, instructor_id)).fetchone()

        if session_info:
            # 2. CRITICAL FIX: Delete dependent enrollments first
            # This satisfies the Foreign Key constraint so we can delete the session
            cursor.execute('DELETE FROM enrollments WHERE session_id = ?', (session_id,))

            # RULE 1: If students were enrolled, put the request back to 'pending'
            if session_info['current_enrollment'] > 0:
                cursor.execute('UPDATE course_requests SET status = "pending" WHERE request_id = ?', 
                               (session_info['request_id'],))
                
                # Now we can safely delete the session
                cursor.execute('DELETE FROM scheduled_sessions WHERE session_id = ?', (session_id,))
                flash("You withdrew. The students were moved back to the pending list.")
            
            # RULE 2: If 0 students, delete the session and the original request entirely
            else:
                cursor.execute('DELETE FROM scheduled_sessions WHERE session_id = ?', (session_id,))
                cursor.execute('DELETE FROM course_requests WHERE request_id = ?', (session_info['request_id'],))
                flash("Session and original request deleted (no students were enrolled).")

            conn.commit()
        else:
            flash("Session not found or unauthorized.")

    except Exception as e:
        conn.rollback()
        # This will help you see if there are still other SQL errors
        flash(f"Error: {e}")
    finally:
        conn.close()

    return redirect(url_for('instructor_dashboard'))

# ROUTE: JOIN SESSION (Student's Dashboard)
@app.route('/join_session/<int:session_id>', methods=['POST']) # Redirects a student to the URL of the session they'll join
def join_session(session_id):
    student_id = session['user_id'] # Their user_id upon login gets set as their student ID for the Enrollments Table
    
    conn = get_db_connection()
    
    # 1. Add the student to the Enrollments table
    # (Note: Our UNIQUE constraint will prevent them from joining twice!)
    try:
        conn.execute('INSERT INTO enrollments (session_id, student_id) VALUES (?, ?)', 
                     (session_id, student_id))
        
        # 2. Increment the current_enrollment count in the session table
        conn.execute('''
            UPDATE scheduled_sessions 
            SET current_enrollment = current_enrollment + 1 
            WHERE session_id = ?
        ''', (session_id,))
        
        conn.commit()
        flash("You have successfully joined the session!")
    except:
        flash("You are already enrolled in this session.")
        
    conn.close()
    return redirect(url_for('student_dashboard'))

# ROUTE: LOGOUT
@app.route('/logout')
def logout():
    session.clear()  # This "forgets" the user_id and role
    flash("You have been logged out.")
    return redirect(url_for('login'))

# Initialize the database and run the app
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5001)
