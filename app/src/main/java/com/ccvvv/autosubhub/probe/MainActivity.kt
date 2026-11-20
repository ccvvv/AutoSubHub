package com.ccvvv.autosubhub.probe

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var status: TextView
    private lateinit var subscriptionInfo: TextView
    private lateinit var tokenInput: EditText
    private lateinit var reportInput: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 28, 32, 28)
        }
        val scroll = ScrollView(this)
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroll.addView(content)
        content.addView(TextView(this).apply {
            text = "AutoSubHub 国内网络探针"
            textSize = 22f
        })
        content.addView(TextView(this).apply {
            text = "阶段 1：订阅读取与 GitHub 报告回传。当前版本不会自动切换代理，也不会生成虚假的测速结果。"
            textSize = 14f
            setPadding(0, 12, 0, 18)
        })
        content.addView(TextView(this).apply { text = "订阅地址"; textSize = 16f })
        content.addView(TextView(this).apply {
            text = SUBSCRIPTION_URL
            textSize = 13f
            setTextIsSelectable(true)
        })
        content.addView(actionButton("读取当前订阅") { loadSubscription() })
        subscriptionInfo = TextView(this).apply {
            text = "尚未读取"
            setPadding(0, 12, 0, 20)
        }
        content.addView(subscriptionInfo)
        content.addView(TextView(this).apply {
            text = "GitHub Fine-grained Token"
            textSize = 16f
        })
        tokenInput = EditText(this).apply {
            hint = "仅授予此仓库必要的 Actions 权限"
            inputType = 129
        }
        content.addView(tokenInput)
        content.addView(TextView(this).apply {
            text = "测试结果 JSON（只允许填写真实测试结果）"
            textSize = 16f
            setPadding(0, 18, 0, 4)
        })
        reportInput = EditText(this).apply {
            minLines = 8
            gravity = Gravity.TOP
            inputType = android.text.InputType.TYPE_CLASS_TEXT or
                android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setText("""[
  {
    "node_id": "replace_with_opaque_id_001",
    "protocol": "shadowsocks",
    "status": "inconclusive",
    "gpt_ok": false,
    "latency_ms": null,
    "download_kbps": null
  }
]""")
        }
        content.addView(reportInput)
        content.addView(actionButton("提交报告到 GitHub") { submitReport() })
        status = TextView(this).apply {
            text = "状态：等待操作"
            textSize = 14f
            setPadding(0, 18, 0, 8)
        }
        content.addView(status)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
    }

    private fun actionButton(label: String, action: () -> Unit) =
        Button(this).apply { text = label; setOnClickListener { action() } }

    private fun loadSubscription() {
        setStatus("正在读取订阅……")
        executor.execute {
            try {
                val connection = (URL(SUBSCRIPTION_URL).openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 15000
                    readTimeout = 20000
                    setRequestProperty("User-Agent", "AutoSubHub-Android-Probe/0.1")
                }
                val code = connection.responseCode
                val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
                    .bufferedReader().use { it.readText() }
                connection.disconnect()
                if (code !in 200..299) error("HTTP $code")
                val count = Regex("(?m)^\\s*-\\s*name\\s*:").findAll(body).count()
                runOnUiThread {
                    subscriptionInfo.text = "读取成功：HTTP $code\n文件大小：${body.toByteArray().size} 字节\n粗略识别节点条目：$count\n注意：订阅可下载不代表节点可连接。"
                    setStatus("订阅读取完成")
                }
            } catch (e: Exception) {
                runOnUiThread { setStatus("订阅读取失败：${e.message ?: e.javaClass.simpleName}") }
            }
        }
    }

    private fun submitReport() {
        val token = tokenInput.text.toString().trim()
        if (token.isEmpty()) { setStatus("请先填写 GitHub Token"); return }
        val results = try { org.json.JSONArray(reportInput.text.toString()) }
        catch (_: Exception) { setStatus("结果 JSON 格式错误"); return }
        if (results.length() !in 1..50) { setStatus("一次必须提交 1～50 条结果"); return }
        val payload = JSONObject().apply {
            put("device_id", "mi6-probe-01")
            put("network", "mainland-wifi")
            put("tested_at", Instant.now().toString())
            put("results", results)
        }
        setStatus("正在提交脱敏报告……")
        executor.execute {
            try {
                val connection = (URL(DISPATCH_URL).openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 15000
                    readTimeout = 20000
                    doOutput = true
                    setRequestProperty("Authorization", "Bearer $token")
                    setRequestProperty("Accept", "application/vnd.github+json")
                    setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                }
                val request = JSONObject().apply {
                    put("event_type", "domestic-probe-results")
                    put("client_payload", payload)
                }.toString()
                connection.outputStream.use { it.write(request.toByteArray(Charsets.UTF_8)) }
                val code = connection.responseCode
                connection.disconnect()
                runOnUiThread {
                    setStatus(if (code == 204) "GitHub 已接收报告（HTTP 204）" else "提交失败：HTTP $code")
                    if (code == 204) tokenInput.text.clear()
                }
            } catch (e: Exception) {
                runOnUiThread { setStatus("提交失败：${e.message ?: e.javaClass.simpleName}") }
            }
        }
    }

    private fun setStatus(message: String) { status.text = "状态：$message" }

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val SUBSCRIPTION_URL = "https://ccvvv.github.io/AutoSubHub/subscription.yaml"
        private const val DISPATCH_URL = "https://api.github.com/repos/ccvvv/AutoSubHub/dispatches"
    }
}
