## **Battery Manager with Barcode Scanning and Video Feed**

This project is a battery management system that uses barcode scanning, a video feed, and status tracking to manage battery usage in real time. The program is built with Python, Flask, and OpenCV, and it displays the live camera feed on a webpage that can be accessed locally or over a network.

## Features:

* Real-time barcode scanning using a webcam.

* Displays the live video feed on a web interface.

* Tracks battery status with automatic cooldown periods.

* Play sound feedback on scan.
                
* Accessible from any device on the same local network.

## Prerequisites:

Ensure you have the following dependencies installed:

Python 3.7+

[pip](https://pip.pypa.io/en/stable/) (Python package manager)

Python packages: [Flask](https://flask.palletsprojects.com/en/stable/), [OpenCV](https://pypi.org/project/opencv-python/), [pygame](https://pypi.org/project/pygame/), and [pyzbar](https://pypi.org/project/pyzbar/)

libzbar0 (for barcode decoding) – only required on Raspberry Pi.

## Setup and Usage on PC

Clone [this](https://github.com/aditya0shah/Battery-Logger) repository to your PC.

Navigate to the project directory and create a virtual environment.

**Install required packages:**

```bash
pip install Flask opencv-python-headless pygame pyzbar panda plotly
```

## Run the Program:

Start the application:
```bash
python main.py
```

Open your browser and go to http://127.0.0.1:5000 to access the web interface
In addition, you can access the logger from any device connected to the same network; go to: IPADDRESS:5000 (for example 192.168.1.10:5000)

## Setup and Usage on Raspberry Pi (Do it in this order)
1. Clone [this](https://github.com/aditya0shah/Battery-Logger) repository to your Raspberry Pi at your desired folder.
   
   command:
   ```bash
    git clone https://github.com/aditya0shah/Battery-Logger
   ```

3. Install Required Packages

4. Make sure Python and pip are installed.

**Create a Virtual Environment:**

Install ```venv``` if Needed:
```bash
sudo apt install python3-venv
```
Create a Virtual Environment:

```bash
python3 -m venv battery_project_env
```
Install the system library libzbar0:
```bash
sudo apt update
sudo apt install libzbar0
```
Find your Raspberry Pi’s IP address:
```bash
hostname -I
```
**Note the IP address (e.g., 192.168.1.10) to access the app from other devices on the same network.*
Activate the Virtual Environment:
```bash
source battery_project_env/bin/activate
```

Install the required Python packages:
```bash
pip install Flask opencv-python-headless pygame pyzbar
```



## Run the Application
Start the app on the Raspberry Pi:

```bash
python main.py
```
On your Raspberry Pi, access the app at 
```bash
http://127.0.0.1:5000 
```
**Alternatively, use the Pi’s IP address (e.g., http://192.168.1.10:5000) to access it from other devices on the same network.*


*Project credit to [5987](https://github.com/DavidMasin/Battery-Logger-5987)*

