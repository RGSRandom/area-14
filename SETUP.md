# Discord Bot Setup - Local & GitHub Actions 24/7

## Quick Start - Run Locally

### Option 1: Double-click (Easiest)
1. Make sure you have Python installed
2. Create `.env` file with your token:
   ```
   BOT_TOKEN=your_discord_bot_token_here
   ```
3. Double-click `run_local.bat` (Windows) or `run_local.ps1` (PowerShell)
4. Done! The bot is running locally

### Option 2: Manual Terminal
```powershell
# Create virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the bot
python main.py
```

---

## Run 24/7 on GitHub Actions

### 1. Create GitHub Repository
```bash
cd c:\Users\anton\Desktop\A14
git init
git add .
git commit -m "Initial bot setup"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/discord-bot.git
git push -u origin main
```

### 2. Add Bot Token to GitHub Secrets
1. Go to your GitHub repo
2. Settings → Secrets and variables → Actions
3. Click "New repository secret"
4. Name: `BOT_TOKEN`
5. Value: (paste your Discord bot token)

### 3. Done!
The bot will now run automatically on GitHub Actions, restarting every 5 hours to stay online 24/7.
