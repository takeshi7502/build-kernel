// PM2 supervises one ordinary Telegram bot, including its dashboard and poller.
const path = require("node:path");
const root = path.resolve(__dirname, "..");

module.exports = {
  apps: [
    {
      name: "gki-bot",
      cwd: root,
      script: path.join(root, "venv", "bin", "python"),
      args: ["-m", "bot"],
      interpreter: "none",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      restart_delay: 5000,
      min_uptime: 10000,
      max_restarts: 10,
      kill_timeout: 15000,
      time: true,
      env: { PYTHONUNBUFFERED: "1" },
    },
  ],
};
