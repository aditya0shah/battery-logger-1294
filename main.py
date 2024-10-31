import cv2
import time
import csv
import threading
from pyzbar.pyzbar import decode
from datetime import datetime, timedelta
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, Response
import json
import os
import pygame
import pandas as pd
import plotly
import plotly.express as px

pygame.mixer.init()
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
# Define the path for persistent data storage
PERSISTENT_FILE = 'battery_status.json'
stop_flag = threading.Event()  # Create an Event object to signal threads to stop

app = Flask(__name__)
app.secret_key = os.urandom(12)

# Define team number default
TEAM_NUMBER = "1294"
# Create a lock for thread safety
battery_status_lock = threading.Lock()

COOLDOWN_DURATION_TIME = 600  # seconds

ADVANCED_LOGGING = True  # Default is on

# Battery status tracking dictionary
battery_status = {}
# List to keep track of pending batteries that are scanned but not in the system
pending_batteries = []

SETTINGS_FILE = 'settings.json'


# Initialize the CSV file and write headers if it doesn’t exist
def initialize_csv():
    if not os.path.exists('battery_log.csv'):
        with open('battery_log.csv', mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([
                'Timestamp',
                'Battery Code',
                'Team Number',
                'Purchase Year',
                'Battery Number',
                'Status',
                'Current Usage (J)',
                'Battery Feel',
                'Charged mAh'
            ])


# Parse battery code
def parse_battery_code(barcode_data):
    team_number = barcode_data[:4]
    purchase_year = barcode_data[4:8]
    battery_number = barcode_data[8:12]

    return {
        "team_number": team_number,
        "purchase_year": purchase_year,
        "battery_number": battery_number
    }


# Log scan data to CSV
def log_to_csv(barcode_data, battery_info, status):
    initialize_csv()  # Ensure CSV is initialized
    with open('battery_log.csv', mode='a', newline='') as file:
        writer = csv.writer(file)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([
            timestamp,
            barcode_data,
            battery_info.get('team_number', ''),
            battery_info.get('purchase_year', ''),
            battery_info.get('battery_number', ''),
            status,
            battery_info.get('current_usage', ''),
            battery_info.get('battery_feel', ''),
            battery_info.get('charged_mAh', '')
        ])


# Update battery status with timestamp
def update_battery_status(barcode_data, new_status):
    battery_status[barcode_data]['status'] = new_status
    battery_status[barcode_data]['display_time'] = timedelta(0)
    battery_status[barcode_data]['last_change'] = datetime.now()
    battery_status[barcode_data]['notes'] = battery_status[barcode_data].get('notes', '')
    battery_status[barcode_data]['usage_count'] = battery_status[barcode_data].get('usage_count', 0)

    if battery_status[barcode_data]['status'] == "In Use":
        battery_status[barcode_data]['usage_count'] += 1

    # Set the awaiting_advanced_input flag based on the new status
    if ADVANCED_LOGGING and new_status in ["In Use", "Charging"]:
        battery_status[barcode_data]['awaiting_advanced_input'] = True
    else:
        # Reset the flag if the status is not "In Use" or "Charging"
        battery_status[barcode_data]['awaiting_advanced_input'] = False


def calculate_average_usage():
    with battery_status_lock:
        total_usage = sum(battery['usage_count'] for battery in battery_status.values())
        battery_count = len(battery_status)
        if battery_count == 0:
            return 0
        average_usage = total_usage / battery_count
        return average_usage


def identify_usage_outliers():
    average_usage = calculate_average_usage()
    overused_batteries = []
    underused_batteries = []

    with battery_status_lock:
        for code, data in battery_status.items():
            usage_count = data['usage_count']
            if usage_count >= average_usage + 2:
                overused_batteries.append(code)
            elif usage_count <= average_usage - 2:
                underused_batteries.append(code)

    return overused_batteries, underused_batteries


def can_change_status(barcode_data, new_status):
    with battery_status_lock:
        if barcode_data in battery_status:
            last_status = battery_status[barcode_data]['status']
            last_change = battery_status[barcode_data]['last_change']

            # Logic to enforce allowed transitions
            valid_transitions = {
                "Charging": ["Cooldown To Robot"],
                "Cooldown To Robot": ["Ready for ROBOT"],
                "Ready for ROBOT": ["In Use"],
                "In Use": ["Cooldown To Charge"],
                "Cooldown To Charge": ["Ready for CHARGING"],
                "Ready for CHARGING": ["Charging"]
            }

            if new_status in valid_transitions.get(last_status, []):
                return True
        else:
            # Allow initialization to "Charging"
            if new_status == "Charging":
                return True
    return False


# Barcode scanning function
def scan_barcode():
    print("Starting barcode scanning...")
    scanned_barcodes = {}
    cooldown_time = 2

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to capture image")
            break

        barcodes = decode(frame)

        for barcode in barcodes:
            barcode_data = barcode.data.decode('utf-8')[:-1]  # Discard last digit

            # Parse the battery code
            try:
                battery_info = parse_battery_code(barcode_data)
            except Exception as e:
                print(f"Invalid barcode format: {barcode_data}")
                continue

            with battery_status_lock:
                if barcode_data not in battery_status:
                    # Battery not in system, add to pending list
                    if barcode_data not in pending_batteries:
                        pending_batteries.append(barcode_data)
                    continue  # Skip further processing

            if barcode_data not in scanned_barcodes or time.time() - scanned_barcodes[barcode_data] > cooldown_time:
                scanned_barcodes[barcode_data] = time.time()
                print(f"Scanned Barcode: {barcode_data}")

                with battery_status_lock:
                    current_status = battery_status.get(barcode_data, {}).get('status', 'Charging')

                # Determine the next status based on current status
                new_status = get_next_status(barcode_data, current_status)
                if new_status:
                    if not ADVANCED_LOGGING:
                        log_to_csv(barcode_data, battery_info, new_status)
                    update_battery_status(barcode_data, new_status)
                    pygame.mixer.music.load("beep.wav")
                    pygame.mixer.music.play()
                else:
                    print(f"Battery {barcode_data} cannot change status yet.")
        time.sleep(0.1)
    cap.release()


# Background thread to auto-update cooldown statuses
def auto_update_cooldown_statuses():
    while True:
        with battery_status_lock:
            for barcode_data, data in battery_status.items():
                status = data['status']
                last_change = data['last_change']
                current_time = datetime.now()

                if status in ["Cooldown To Robot", "Cooldown To Charge"]:
                    # Calculate remaining cooldown time as a countdown timer
                    elapsed_time = current_time - last_change
                    display_time = max(timedelta(seconds=COOLDOWN_DURATION_TIME) - elapsed_time, timedelta(0))
                    hours = int(display_time.total_seconds() // 3600)
                    minutes = int((display_time.total_seconds() % 3600) // 60)
                    seconds = int(display_time.total_seconds() % 60)

                    battery_status[barcode_data]['display_time'] = f"{hours}:{minutes:02}:{seconds:02}"

                    # If countdown reaches zero, change status to ready
                    if display_time == timedelta(0):
                        new_status = "Ready for ROBOT" if status == "Cooldown To Robot" else "Ready for CHARGING"
                        update_battery_status(barcode_data, new_status)

                else:
                    # Show elapsed time as a timer going up
                    elapsed_time = current_time - last_change
                    hours = int(elapsed_time.total_seconds() // 3600)
                    minutes = int((elapsed_time.total_seconds() % 3600) // 60)
                    seconds = int(elapsed_time.total_seconds() % 60)

                    battery_status[barcode_data]['display_time'] = f"{hours}:{minutes:02}:{seconds:02}"

        time.sleep(1)  # Check every second for countdown accuracy


def format_battery_code(code):
    # Format battery code as TEAM-YEAR-NUMB
    return f"{code[:4]}-{code[4:8]}-{code[8:]}"


def get_next_status(barcode_data, current_status):
    # Logic to enforce allowed transitions
    valid_transitions = {
        "Charging": "Cooldown To Robot",
        "Cooldown To Robot": "Ready for ROBOT",
        "Ready for ROBOT": "In Use",
        "In Use": "Cooldown To Charge",
        "Cooldown To Charge": "Ready for CHARGING",
        "Ready for CHARGING": "Charging"
    }

    next_status = valid_transitions.get(current_status)

    # Implement any cooldown checks or additional logic here if necessary
    # For simplicity, we'll assume the transition is allowed
    return next_status


# Flask route to display battery statuses
@app.route('/')
def index():
    average_usage = calculate_average_usage()
    overused_batteries, underused_batteries = identify_usage_outliers()

    with battery_status_lock:
        battery_info = [
            {
                'battery_code': code,
                'status': data['status'],
                'display_time': str(data.get('display_time', '00:00:00')),
                'last_change': data['last_change'].strftime("%Y-%m-%d %H:%M:%S"),
                'usage_count': data.get('usage_count', 0),
                'notes': data.get('notes', '')
            }
            for code, data in battery_status.items()
        ]

    # Display warnings
    if overused_batteries:
        overused_list = ', '.join(format_battery_code(code) for code in overused_batteries)
        flash(f'The following batteries are overused (usage more than average + 2): {overused_list}', 'warning')

    if underused_batteries:
        underused_list = ', '.join(format_battery_code(code) for code in underused_batteries)
        flash(f'The following batteries are underused (usage less than average - 2): {underused_list}', 'warning')

    return render_template('index.html', batteries=battery_info, format_battery_code=format_battery_code)


@app.route('/statistics')
def statistics():
    # Load the battery log data
    df = pd.read_csv('battery_log.csv')

    if df.empty:
        flash("No data available for statistics.", "warning")
        return redirect(url_for('index'))

    # Convert 'Timestamp' to datetime
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])

    # Generate graphs
    graphs = []



    charged_data = df[df['Charged mAh'].notnull()]
    fig_charged = px.line(charged_data, x='Timestamp', y='Charged mAh', color='Battery Code',
                          title='Charged mAh Over Time')
    graphJSON_charged = json.dumps(fig_charged, cls=plotly.utils.PlotlyJSONEncoder)
    graphs.append(graphJSON_charged)

    charge_used = df[df['Current Usage (J)'].notnull()]
    fig_charged_used = px.line(charge_used, x='Timestamp', y='Current Usage (J)', color='Battery Code',
                               title='Current Usage (J) Over Time')
    graphJSON_charged_used = json.dumps(fig_charged_used, cls=plotly.utils.PlotlyJSONEncoder)
    graphs.append(graphJSON_charged_used)

    # Render the template with the graphs
    return render_template('statistics.html', graphs=graphs, advanced_logging=ADVANCED_LOGGING)



@app.route('/battery_statistics/<battery_code>')
def battery_statistics(battery_code):
    # Load the battery log data
    df = pd.read_csv('battery_log.csv')
    df['Battery Code'] = df['Battery Code'].astype(str)

    # Filter data for the specific battery
    battery_df = df[df['Battery Code'] == battery_code]

    print("Unique Battery Codes in DataFrame:")
    print(df['Battery Code'].unique())
    if battery_df.empty:
        flash(f"No data available for battery {battery_code}.", "warning")
        return redirect(url_for('index'))

    # Convert 'Timestamp' to datetime if not already
    battery_df['Timestamp'] = pd.to_datetime(battery_df['Timestamp'])

    # Generate graphs
    graphs = []

    # Example Graph 1: Battery Usage Over Time
    usage_over_time = battery_df[battery_df['Status'] == 'In Use']
    usage_over_time['Timestamp'] = pd.to_datetime(usage_over_time['Timestamp'], errors='coerce')
    usage_over_time['Current Usage (J)'] = pd.to_numeric(usage_over_time['Current Usage (J)'], errors='coerce')
    usage_over_time = usage_over_time.dropna(subset=['Timestamp', 'Current Usage (J)'])
    fig_charged = px.line(
        usage_over_time,
        x='Timestamp',
        y='Current Usage (J)',
        title='Current Usage (J) Over Time',
        markers=True,
        color=None,
        line_group=None
    )
    graphJSON_usage = json.dumps(fig_charged, cls=plotly.utils.PlotlyJSONEncoder)
    graphs.append(graphJSON_usage)

    # Example Graph 2: Charged mAh Over Time
    charged_over_time = battery_df[battery_df['Status'] == 'Charging']
    print(f"Number of data points: {len(charged_over_time)}")
    charged_over_time['Timestamp'] = pd.to_datetime(charged_over_time['Timestamp'], errors='coerce')
    charged_over_time['Charged mAh'] = pd.to_numeric(charged_over_time['Charged mAh'], errors='coerce')
    charged_over_time = charged_over_time.dropna(subset=['Timestamp', 'Charged mAh'])

    fig_charged = px.line(
        charged_over_time,
        x='Timestamp',
        y='Charged mAh',
        title='Charged mAh Over Time',
        markers=True,
        color=None,
        line_group=None
    )
    graphJSON_charged = json.dumps(fig_charged, cls=plotly.utils.PlotlyJSONEncoder)
    graphs.append(graphJSON_charged)

    # Example Graph 3: Battery Feel Ratings Over Time
    battery_feel_data = battery_df[battery_df['Battery Feel'].notnull()]
    fig_feel = px.line(
        battery_feel_data,
        x='Timestamp',
        y='Battery Feel',
        title='Battery Feel Over Time',
        markers=True,
        color=None,
        line_group=None
    )
    graphJSON_feel = json.dumps(fig_feel, cls=plotly.utils.PlotlyJSONEncoder)
    graphs.append(graphJSON_feel)

    # Render the template with the graphs
    return render_template('battery_statistics.html', battery_code=battery_code, graphs=graphs,
                           format_battery_code=format_battery_code)


def load_settings():
    global COOLDOWN_DURATION_TIME
    global TEAM_NUMBER
    global ADVANCED_LOGGING
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            COOLDOWN_DURATION_TIME = settings.get('cooldown_duration_time', COOLDOWN_DURATION_TIME)
            TEAM_NUMBER = settings.get('team_number', TEAM_NUMBER)
            ADVANCED_LOGGING = settings.get('advanced_logging', ADVANCED_LOGGING)
    except FileNotFoundError:
        # Settings file does not exist, keep default settings
        pass
    except json.JSONDecodeError:
        # Settings file is corrupt or invalid, handle as needed
        pass


def save_settings():
    settings = {
        'cooldown_duration_time': COOLDOWN_DURATION_TIME,
        'team_number': TEAM_NUMBER,
        'advanced_logging': ADVANCED_LOGGING
    }
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f)


@app.route('/api/advanced_logging_input', methods=['POST'])
def advanced_logging_input():
    print("Advanced logging input received")
    data = request.json
    battery_code = data.get('battery_code')
    with battery_status_lock:
        if battery_code in battery_status:
            # Save the data without checking awaiting_advanced_input
            if 'current_usage' in data and 'battery_feel' in data:
                battery_status[battery_code]['current_usage'] = data['current_usage']
                battery_status[battery_code]['battery_feel'] = data['battery_feel']
            elif 'charged_mAh' in data:
                battery_status[battery_code]['charged_mAh'] = data['charged_mAh']
            else:
                return jsonify({'success': False, 'message': 'Invalid data provided.'}), 400

            # Remove the awaiting_advanced_input flag
            battery_status[battery_code]['awaiting_advanced_input'] = False

            # Optionally, log this data to CSV
            log_to_csv(battery_code, battery_status[battery_code], battery_status[battery_code]['status'])
            print("LOGGED AT " + str(time.time()))
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Battery not found.'}), 404


@app.route('/api/status_changes')
def status_changes():
    with battery_status_lock:
        # Return batteries that have changed status and require advanced logging input
        if ADVANCED_LOGGING:
            changes = []
            for code, data in battery_status.items():
                if data.get('awaiting_advanced_input', False):
                    changes.append({
                        'battery_code': code,
                        'status': data['status']
                    })
            return jsonify(changes)
        else:
            return jsonify([])


# Flask route for manual battery code entry
@app.route('/manual_entry', methods=['POST'])
def manual_entry():
    battery_code = request.form.get('battery_code')

    if not battery_code:
        flash('Please enter a battery code.', 'error')
        return redirect(url_for('index'))

    battery_code = battery_code.strip().replace('-', '')

    # Parse the battery code
    try:
        battery_info = parse_battery_code(battery_code)
    except Exception as e:
        flash('Invalid battery code format.', 'error')
        return redirect(url_for('index'))

    with battery_status_lock:
        if battery_code not in battery_status:
            # Battery not found, render a template asking to add it
            return render_template('add_battery_prompt.html', battery_code=battery_code, battery_info=battery_info)
        else:
            # Existing logic for updating battery status
            current_status = battery_status[battery_code]['status']
            # Determine the next status based on current status
            new_status = get_next_status(battery_code, current_status)
            if new_status:
                if not ADVANCED_LOGGING:
                    log_to_csv(battery_code, battery_info, new_status)
                update_battery_status(battery_code, new_status)
                flash(f"Battery {battery_code} status updated to {new_status}.", 'success')
            else:
                flash(f"Battery {battery_code} cannot change status yet.", 'error')
            return redirect(url_for('index'))


@app.route('/api/get_battery_info/<battery_code>')
def get_battery_info(battery_code):
    battery_code = battery_code.strip()
    with battery_status_lock:
        if battery_code in battery_status:
            data = battery_status[battery_code]
            battery_info = {
                'battery_code': battery_code,
                'status': data['status'],
                'notes': data.get('notes', '')
            }
            return jsonify(battery_info)
        else:
            return jsonify({'error': 'Battery not found'}), 404


@app.route('/edit_battery', methods=['POST'])
def edit_battery():
    with battery_status_lock:
        original_battery_code = request.form.get('original_battery_code').strip()
        new_battery_code = request.form.get('battery_code').strip()
        new_status = request.form.get('status')
        notes = request.form.get('notes', '').strip()

        if original_battery_code != new_battery_code:
            # Handle renaming of battery code
            if new_battery_code in battery_status:
                flash('Battery code already exists.', 'error')
                return redirect(url_for('index'))
            battery_status[new_battery_code] = battery_status.pop(original_battery_code)

        # Update status and notes
        battery_status[new_battery_code]['status'] = new_status
        battery_status[new_battery_code]['notes'] = notes
        battery_status[new_battery_code]['last_change'] = datetime.now()
        battery_status[new_battery_code]['display_time'] = timedelta(0)

        # Save changes
        save_battery_status()

        flash(f'Battery {new_battery_code} has been updated.', 'success')
    return redirect(url_for('index'))


@app.route('/confirm_add_battery', methods=['POST'])
def confirm_add_battery():
    battery_code = request.form.get('battery_code')

    if not battery_code:
        flash('Battery code is missing.', 'error')
        return redirect(url_for('index'))

    battery_code = battery_code.strip().replace('-', '')

    # Parse the battery code
    try:
        battery_info = parse_battery_code(battery_code)
    except Exception as e:
        flash('Invalid battery code format.', 'error')
        return redirect(url_for('index'))

    with battery_status_lock:
        if battery_code in battery_status:
            flash('Battery already exists in the system.', 'error')
            return redirect(url_for('index'))

        # Add the battery to the system with an initial status
        battery_status[battery_code] = {
            'status': 'Charging',
            'last_change': datetime.now(),
            'display_time': timedelta(0),
            'usage_count': 0,  # Initialize usage count
            'notes': '',  # If you have notes
            'current_usage': None,  # Add this line
            'battery_feel': None,  # Add this line
            'charged_mAh': None  # Add this line
        }

        # Optionally, log this action
        log_to_csv(battery_code, battery_info, 'Added to System')

    flash(f'Battery {battery_code} has been added to the system.', 'success')
    return redirect(url_for('index'))


@app.route('/api/confirm_add_battery', methods=['POST'])
def api_confirm_add_battery():
    battery_code = request.json.get('battery_code')

    if not battery_code:
        return jsonify({'success': False, 'message': 'Battery code is missing.'})

    battery_code = battery_code.strip().replace('-', '')

    # Parse the battery code
    try:
        battery_info = parse_battery_code(battery_code)
    except Exception as e:
        return jsonify({'success': False, 'message': 'Invalid battery code format.'})

    with battery_status_lock:
        if battery_code in battery_status:
            return jsonify({'success': False, 'message': 'Battery already exists in the system.'})

        # Add the battery to the system with an initial status
        battery_status[battery_code] = {
            'status': 'Charging',
            'last_change': datetime.now(),
            'display_time': timedelta(0),
            'usage_count': 0,  # Initialize usage count
            'notes': '',  # If you have notes
            'current_usage': None,  # Add this line
            'battery_feel': None,  # Add this line
            'charged_mAh': None  # Add this line  # If you have notes
        }

        # Remove from pending batteries
        if battery_code in pending_batteries:
            pending_batteries.remove(battery_code)

        # Optionally, log this action
        log_to_csv(battery_code, battery_info, 'Added to System')

    return jsonify({'success': True, 'message': f'Battery {battery_code} has been added to the system.'})


# API endpoint to provide battery status as JSON
@app.route('/api/battery_status')
def battery_status_api():
    with battery_status_lock:
        battery_info = [
            {
                'battery_code': code,
                'status': data['status'],
                'display_time': str(data.get('display_time', '00:00:00')),
                'last_change': data['last_change'].strftime("%Y-%m-%d %H:%M:%S"),
                'notes': data.get('notes', '')  # Include 'notes' in the API response
            } for code, data in battery_status.items()
        ]
    return jsonify(battery_info)


@app.route('/api/pending_batteries')
def get_pending_batteries():
    with battery_status_lock:
        return jsonify(pending_batteries)


@app.route('/api/remove_pending_battery', methods=['POST'])
def remove_pending_battery():
    battery_code = request.json.get('battery_code')
    if battery_code:
        with battery_status_lock:
            if battery_code in pending_batteries:
                pending_batteries.remove(battery_code)
    return jsonify({'success': True})


@app.route('/logs')
def logs():
    logs = []
    # Read the CSV file
    try:
        with open('battery_log.csv', mode='r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                logs.append(row)
    except FileNotFoundError:
        flash("Log file not found.", "error")

    # Pass logs data to the template
    return render_template('logs.html', logs=logs)


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


def generate_frames():
    while True:
        success, frame = cap.read()
        if not success:
            break
        else:
            # Encode the frame as JPEG
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()

            # Yield the frame as part of the response
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    cap.release()


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    global COOLDOWN_DURATION_TIME
    global TEAM_NUMBER
    global ADVANCED_LOGGING

    if request.method == 'POST':
        # Retrieve and apply settings
        try:
            COOLDOWN_DURATION_TIME = int(request.form.get('cooldown_time', COOLDOWN_DURATION_TIME))
            TEAM_NUMBER = request.form.get('team_number', TEAM_NUMBER)
            ADVANCED_LOGGING = 'advanced_logging' in request.form
            flash("Settings have been updated.", "success")
            save_settings()  # Save settings to JSON file
        except:
            try:
                TEAM_NUMBER = request.form.get('team_number', TEAM_NUMBER)
                flash("Settings have been updated (ONLY TEAM NUMBER)", "success")
                save_settings()  # Save settings to JSON file

            except:
                try:
                    COOLDOWN_DURATION_TIME = int(request.form.get('cooldown_time', COOLDOWN_DURATION_TIME))
                    flash("Settings have been updated (ONLY COOLDOWN )", "success")
                    save_settings()  # Save settings to JSON file
                except:
                    try:
                        ADVANCED_LOGGING = 'advanced_logging' in request.form
                        flash("Settings have been updated (ONLY Advanced Logging toggle)", "success")
                        save_settings()  # Save settings to JSON file
                    except:
                        flash("Settings have NOT been updated.", "warning")
                        save_settings()  # Save settings to JSON file

    return render_template('settings.html',
                           advanced_logging=ADVANCED_LOGGING,
                           cooldown_time=COOLDOWN_DURATION_TIME,
                           team_number=TEAM_NUMBER)


@app.route('/add_battery', methods=['POST'])
def add_battery():
    global TEAM_NUMBER

    # Get current year
    current_year = datetime.now().year

    # Initialize battery number as a string with leading zeros
    number = 1
    battery_number = f"{number:03d}"  # Format number as "001"
    battery_code = f"{current_year}{battery_number}"

    # Increment battery number if code already exists
    while battery_code in battery_status:
        number += 1
        battery_number = f"{number:03d}"  # Format number with leading zeros
        battery_code = f"{current_year}{battery_number}"

    # Add the new battery to `battery_status`
    battery_status[battery_code] = {
        'status': 'Charging',
        'last_change': datetime.now(),
        'display_time': timedelta(0),
        'usage_count': 0,  # Initialize usage count
        'notes': '',  # If you have notes
        'current_usage': None,  # Add this line
        'battery_feel': None,  # Add this line
        'charged_mAh': None  # Add this line  # If you have notes
    }

    # Return a JSON response
    return jsonify({'message': f"Battery {battery_code} added successfully."})


@app.route('/stop', methods=['POST'])
def stop_system():
    stop_flag.set()  # Set the stop flag to terminate background threads
    flash("System stopped successfully. Shutting down...", "success")

    # Give a moment for flash message to register
    time.sleep(1)

    # Exit the program
    save_battery_status()
    os.abort()  # Forcefully terminate the Flask server and Python process
    # Alternatively, use sys.exit() but note that os._exit(0) ensures immediate termination


@app.route('/delete_battery', methods=['POST'])
def delete_battery():
    battery_code = request.form.get('battery_code', '').strip()

    if not battery_code:
        flash('Battery code is required to delete a battery.', 'error')
        return redirect(url_for('index'))

    with battery_status_lock:
        if battery_code in battery_status:
            del battery_status[battery_code]
            # Optionally, save the updated battery status
            save_battery_status()
            flash(f'Battery {battery_code} has been deleted.', 'success')
        else:
            flash(f'Battery {battery_code} not found.', 'error')

    return redirect(url_for('index'))


def save_battery_status():
    with open(PERSISTENT_FILE, 'w') as f:
        data_to_save = {
            code: {
                'status': data['status'],
                'last_change': data['last_change'].strftime("%Y-%m-%d %H:%M:%S"),
                'usage_count': data.get('usage_count', 0),
                'notes': data.get('notes', ''),
                'current_usage': data.get('current_usage'),
                'battery_feel': data.get('battery_feel'),
                'charged_mAh': data.get('charged_mAh'),
                'awaiting_advanced_input': data.get('awaiting_advanced_input', False)
            }
            for code, data in battery_status.items()
        }
        json.dump(data_to_save, f)


def load_initial_battery_status():
    if os.path.exists(PERSISTENT_FILE):
        with open(PERSISTENT_FILE, 'r') as f:
            data_loaded = json.load(f)
            for code, data in data_loaded.items():
                battery_status[code] = {
                    'status': data['status'],
                    'last_change': datetime.strptime(data['last_change'], "%Y-%m-%d %H:%M:%S"),
                    'display_time': timedelta(0),
                    'usage_count': data.get('usage_count', 0),
                    'notes': data.get('notes', ''),
                    'current_usage': data.get('current_usage'),
                    'battery_feel': data.get('battery_feel'),
                    'charged_mAh': data.get('charged_mAh'),
                    'awaiting_advanced_input': data.get('awaiting_advanced_input', False)
                }


# Start the Flask app and background tasks
if __name__ == "__main__":
    # Load settings from file
    load_settings()
    # Load initial battery status from persistent file
    load_initial_battery_status()

    # Initialize CSV if necessary
    initialize_csv()

    # Start the barcode scanning in a background thread
    scanning_thread = threading.Thread(target=scan_barcode, daemon=True)
    scanning_thread.start()

    # Start the auto-update cooldown statuses in a background thread
    cooldown_thread = threading.Thread(target=auto_update_cooldown_statuses, daemon=True)
    cooldown_thread.start()

    try:
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    finally:
        # Save battery status to persistent file on exit
        save_battery_status()
        save_settings()
