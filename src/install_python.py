import os
import requests
import subprocess
import sys

def is_admin():
    """Check if the script is run with administrator privileges."""
    try:
        return os.getuid() == 0  # This works on Unix-like systems
    except AttributeError:
        # On Windows, check if the script is running with admin privileges
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()

def download_python_installer():
    # URL for the Python 3.10 installer (64-bit for Windows)
    url = "https://www.python.org/ftp/python/3.10.0/python-3.10.0-amd64.exe"
    installer_path = "python-3.10.0-amd64.exe"

    # Download the installer
    print("Downloading Python 3.10 installer...")
    response = requests.get(url)
    with open(installer_path, 'wb') as file:
        file.write(response.content)
    print("Download complete.")

    return installer_path

def install_python(installer_path):
    # Run the installer with the option to add Python to PATH
    print("Installing Python 3.10...")
    subprocess.run([installer_path, "/quiet", "InstallAllUsers=1", "PrependPath=1"], check=True)
    print("Python 3.10 installation complete.")

def main():
    # Check if the script is run with administrator privileges
    if not is_admin():
        print("Please run this script as an administrator.")
        sys.exit(1)

    installer_path = download_python_installer()
    install_python(installer_path)

    # Clean up the installer file
    os.remove(installer_path)
    print("Installer removed.")

if __name__ == "__main__":
    main()