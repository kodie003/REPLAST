# Getting the REPLAST code onto the Pi (one time)

The code lives on GitHub, not on the Pi. You first **download (clone)** it once;
after that you only **update (pull)** it.

## 1. Install the basics

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip g++
```

## 2. Download the code

```bash
cd ~
git clone https://github.com/kodie003/REPLAST.git
cd REPLAST
git checkout claude/trusting-goldberg-nb73a0
```

Expected: `Switched to a new branch 'claude/trusting-goldberg-nb73a0'`.

If git asks for a **username/password** (the repo is private): the username is
your GitHub username, and the "password" must be a **personal access token**,
not your GitHub password. Make one at GitHub → Settings → Developer settings →
Personal access tokens → *Tokens (classic)* → Generate, tick `repo`, copy it,
paste it as the password.

## 3. Make a Python environment and install the packages

Ubuntu 24.04 does not allow `pip install` system-wide, so use a virtual
environment (a private folder of packages just for this project):

```bash
cd ~/REPLAST/pi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Your prompt now starts with `(.venv)`. Do `source .venv/bin/activate` again in
every new terminal before running REPLAST code.

## 4. Check it works

```bash
pytest -q
```

Expected last line: `113 passed` (the number grows over time).

## 5. Allow access to the Arduino's port (once)

```bash
sudo usermod -aG dialout $USER
sudo reboot
```

## Later: getting the newest code

```bash
cd ~/REPLAST
git pull
```
