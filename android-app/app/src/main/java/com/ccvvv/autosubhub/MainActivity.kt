package com.ccvvv.autosubhub

import android.app.Activity
import android.app.PendingIntent
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

class MainActivity : Activity(), SharedPreferences.OnSharedPreferenceChangeListener {
    private lateinit var output: TextView
    private lateinit var progressTitle: TextView
    private lateinit var progressBar: ProgressBar
    private lateinit var progressStats: TextView
    private val prefs by lazy { getSharedPreferences("console", MODE_PRIVATE) }
    private val handler = Handler(Looper.getMainLooper())
    private val pollRunnable = object : Runnable {
        override fun run() {
            if (!prefs.getBoolean("probe_active", false) || !::output.isInitialized) return
            runTermux("progress", showFeedback = false)
            handler.postDelayed(this, 2000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        render()
        prefs.registerOnSharedPreferenceChangeListener(this)
        if (checkSelfPermission("com.termux.permission.RUN_COMMAND") != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf("com.termux.permission.RUN_COMMAND"), 41)
        }
    }

    override fun onResume() {
        super.onResume()
        if (::output.isInitialized) {
            output.text = prefs.getString("last_result", "等待操作。\\n请先完成 Termux 授权设置。")
            updateProgressFromText(output.text.toString())
        }
        if (prefs.getBoolean("probe_active", false)) startProgressPolling()
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(pollRunnable)
    }

    override fun onDestroy() {
        prefs.unregisterOnSharedPreferenceChangeListener(this)
        handler.removeCallbacks(pollRunnable)
        super.onDestroy()
    }

    override fun onSharedPreferenceChanged(sharedPreferences: SharedPreferences, key: String?) {
        if (key == "last_result") {
            val text = prefs.getString("last_result", "").orEmpty()
            output.text = text
            updateProgressFromText(text)
        }
    }

    private fun render() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 36, 28, 24)
        }
        val title = TextView(this).apply {
            text = "AutoSubHub 探针控制台"
            textSize = 24f
        }
        val hint = TextView(this).apply {
            text = "纯手动模式 · Termux 后台执行"
            textSize = 14f
            setPadding(0, 8, 0, 12)
        }
        progressTitle = TextView(this).apply {
            text = "测速进度：尚未开始"
            textSize = 17f
            setPadding(0, 8, 0, 8)
        }
        progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            progress = 0
        }
        progressStats = TextView(this).apply {
            text = "已检测：0 / 0\\n可用：0    失败：0\\n通过率：0.0%"
            textSize = 14f
            setPadding(0, 8, 0, 16)
        }
        root.addView(title)
        root.addView(hint)
        root.addView(progressTitle)
        root.addView(progressBar, LinearLayout.LayoutParams(-1, 18))
        root.addView(progressStats)
        addAction(root, "1. 检查候选版本", "check")
        addAction(root, "2. 开始测速（并发 200 / 超时 5 秒）", "probe")
        addAction(root, "3. 上传测速回执（SSH）", "upload")
        addAction(root, "停止当前测速", "stop")
        addAction(root, "刷新运行日志", "logs")
        output = TextView(this).apply {
            textSize = 13f
            setTextIsSelectable(true)
            text = prefs.getString("last_result", "等待操作。")
            setPadding(0, 16, 0, 0)
        }
        val scroll = ScrollView(this).apply { addView(output) }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
    }

    private fun updateProgressFromText(text: String) {
        val start = text.indexOf('{')
        val end = text.lastIndexOf('}')
        if (start < 0 || end <= start) return
        try {
            val data = JSONObject(text.substring(start, end + 1))
            val status = data.optString("status", "idle")
            val done = data.optInt("done", 0)
            val total = data.optInt("total", 0)
            val passed = data.optInt("passed", 0)
            val failed = data.optInt("failed", 0)
            val elapsed = data.optDouble("elapsed_seconds", 0.0)
            val rate = data.optDouble("success_rate", 0.0)
            val percent = if (total > 0) (done * 100 / total).coerceIn(0, 100) else 0
            progressBar.isIndeterminate = status == "preparing"
            progressBar.progress = percent
            val statusText = when (status) {
                "preparing" -> "正在准备候选节点…"
                "running" -> "正在测速 · $percent%"
                "completed" -> "测速完成"
                "interrupted" -> "测速已中断"
                "failed" -> "测速失败"
                else -> "测速进度：尚未开始"
            }
            progressTitle.text = statusText
            progressStats.text = "已检测：$done / $total\\n可用：$passed    失败：$failed\\n通过率：$rate%    耗时：${elapsed} 秒"
            if (status == "completed" || status == "interrupted" || status == "failed") {
                prefs.edit().putBoolean("probe_active", false).apply()
                handler.removeCallbacks(pollRunnable)
            }
        } catch (_: Exception) {
            // Ignore incomplete or non-JSON command output; the next poll will refresh it.
        }
    }

    private fun startProgressPolling() {
        handler.removeCallbacks(pollRunnable)
        handler.postDelayed(pollRunnable, 1000)
    }

    private fun addAction(root: LinearLayout, label: String, action: String) {
        val button = Button(this).apply {
            text = label
            setOnClickListener {
                if (action == "probe") {
                    prefs.edit().putBoolean("probe_active", true).apply()
                    progressTitle.text = "正在启动测速…"
                    progressBar.isIndeterminate = true
                    startProgressPolling()
                } else if (action == "stop") {
                    prefs.edit().putBoolean("probe_active", false).apply()
                    handler.removeCallbacks(pollRunnable)
                }
                runTermux(action)
            }
        }
        root.addView(button, LinearLayout.LayoutParams(-1, -2))
    }

    private fun runTermux(action: String, showFeedback: Boolean = true) {
        if (checkSelfPermission("com.termux.permission.RUN_COMMAND") != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "请先授予 Termux 命令权限", Toast.LENGTH_LONG).show()
            requestPermissions(arrayOf("com.termux.permission.RUN_COMMAND"), 41)
            return
        }
        val command = when (action) {
            "check" -> "check"
            "probe" -> "probe"
            "upload" -> "upload"
            "stop" -> "stop"
            "progress" -> "progress"
            else -> "logs"
        }
        val intent = Intent("com.termux.RUN_COMMAND").apply {
            setClassName("com.termux", "com.termux.app.RunCommandService")
            putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash")
            putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arrayOf("/data/data/com.termux/files/home/AutoSubHub/app_control.sh", command))
            putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home/AutoSubHub")
            putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
            putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "3")
            putExtra("com.termux.RUN_COMMAND_COMMAND_LABEL", "AutoSubHub: $command")
            putExtra("com.termux.RUN_COMMAND_COMMAND_DESCRIPTION", "Manual AutoSubHub task")
        }
        val callback = Intent(this, CommandResultReceiver::class.java).apply {
            putExtra("command", command)
            setAction("com.ccvvv.autosubhub.RESULT.$command.${System.currentTimeMillis()}")
        }
        val requestCode = nextId.incrementAndGet()
        val flags = PendingIntent.FLAG_ONE_SHOT or if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        val pending = PendingIntent.getBroadcast(this, requestCode, callback, flags)
        intent.putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pending)
        try {
            startService(intent)
            if (showFeedback) output.text = "已发送命令：$command\\n等待 Termux 返回结果……"
        } catch (e: Exception) {
            output.text = "调用 Termux 失败：${e.message}\\n请确认已安装 Termux，并开启 allow-external-apps。"
        }
    }

    companion object {
        private val nextId = AtomicInteger(100)
    }
}
