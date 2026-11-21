package com.ccvvv.autosubhub

import android.app.Activity
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.util.concurrent.atomic.AtomicInteger

class MainActivity : Activity() {
    private lateinit var output: TextView
    private val prefs by lazy { getSharedPreferences("console", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        render()
        if (checkSelfPermission("com.termux.permission.RUN_COMMAND") != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf("com.termux.permission.RUN_COMMAND"), 41)
        }
    }

    override fun onResume() {
        super.onResume()
        if (::output.isInitialized) output.text = prefs.getString("last_result", "等待操作。\n请先完成 Termux 授权设置。")
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
            setPadding(0, 8, 0, 20)
        }
        root.addView(title)
        root.addView(hint)
        addAction(root, "1. 检查候选版本", "check")
        addAction(root, "2. 开始测速（并发 200 / 超时 5 秒）", "probe")
        addAction(root, "3. 上传测速回执（SSH）", "upload")
        addAction(root, "停止当前测速", "stop")
        addAction(root, "刷新运行日志", "logs")
        output = TextView(this).apply {
            textSize = 13f
            setTextIsSelectable(true)
            text = prefs.getString("last_result", "等待操作。")
            setPadding(0, 20, 0, 0)
        }
        val scroll = ScrollView(this).apply { addView(output) }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
    }

    private fun addAction(root: LinearLayout, label: String, action: String) {
        val button = Button(this).apply {
            text = label
            setOnClickListener { runTermux(action) }
        }
        root.addView(button, LinearLayout.LayoutParams(-1, -2))
    }

    private fun runTermux(action: String) {
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
            output.text = "已发送命令：$command\n等待 Termux 返回结果……"
        } catch (e: Exception) {
            output.text = "调用 Termux 失败：${e.message}\n请确认已安装 Termux，并开启 allow-external-apps。"
        }
    }

    companion object {
        private val nextId = AtomicInteger(100)
    }
}
