 #!/bin/bash
cp /boot/firmware/main.py /home/pi/main.py
chown pi:pi /home/pi/main.py
(crontab -l 2>/dev/null; echo "@reboot python3 /home/pi/main.py &") | crontab -u pi -

