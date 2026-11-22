package com.ccvvv.autosubhub

import android.app.Activity
import android.app.PendingIntent
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.*
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

class MainActivity : Activity(), SharedPreferences.OnSharedPreferenceChangeListener {
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var progressInfo: TextView
    private lateinit var nodeInfo: TextView
    private lateinit var speedInfo: TextView
    private lateinit var appsInfo: TextView
    private lateinit var candidateInfo: TextView
    private lateinit var logPanel: LinearLayout
    private lateinit var logText: TextView
    private lateinit var workersSpinner: Spinner
    private val statViews = mutableMapOf<String, TextView>()
    private val actionButtons = mutableMapOf<String, Button>()
    private val prefs by lazy { getSharedPreferences("console", MODE_PRIVATE) }
    private val handler = Handler(Looper.getMainLooper())
    private var monitoringTask = false
    private var seenRunningThisSession = false
    private var taskState = "idle"
    private val poll = object : Runnable {
        override fun run() {
            if (!monitoringTask) return
            runTermux("task_status", false)
            handler.postDelayed(this, 1200)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        render()
        prefs.registerOnSharedPreferenceChangeListener(this)
        if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission("com.termux.permission.RUN_COMMAND") != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf("com.termux.permission.RUN_COMMAND"), 41)
    }

    override fun onResume() {
        super.onResume()
        logText.text = prefs.getString("last_result", "日志暂时为空")
        startPolling()
    }

    override fun onPause() { handler.removeCallbacks(poll); super.onPause() }
    override fun onDestroy() { prefs.unregisterOnSharedPreferenceChangeListener(this); handler.removeCallbacks(poll); super.onDestroy() }

    override fun onSharedPreferenceChanged(p: SharedPreferences, key: String?) {
        if (key == "last_result") {
            val value = prefs.getString("last_result", "").orEmpty()
            val command = prefs.getString("last_command", "").orEmpty()
            logText.text = value
            when (command) {
                "task_status" -> updateTaskStatus(value)
                "progress" -> updateProgress(value)
            }
        }
    }

    private fun render() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.rgb(246,248,251)) }
        val scroll = ScrollView(this)
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18),dp(22),dp(18),dp(28)) }
        body.addView(label("AutoSubHub", 27, true, Color.rgb(24,35,52)))
        body.addView(label("手机节点实测 · 控制面板", 14, false, Color.rgb(105,117,134)))
        val card = card()
        status = label("● 等待开始",17,true,Color.rgb(50,105,80))
        progressInfo = label("已检测 0 / 0",14,false,Color.DKGRAY)
        progress = ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal).apply { max=100; progress=0 }
        card.addView(status); card.addView(progressInfo)
        card.addView(progress,LinearLayout.LayoutParams(-1,dp(9)))
        body.addView(card)

        val stats = LinearLayout(this).apply { orientation=LinearLayout.HORIZONTAL }
        stats.addView(stat("已检测","0")); stats.addView(stat("可用","0"))
        stats.addView(stat("失败","0")); stats.addView(stat("通过率","0%"))
        body.addView(stats)

        val detail = card()
        detail.addView(label("当前检测",16,true,Color.rgb(37,49,66)))
        nodeInfo = detailLine("当前节点：等待测速")
        speedInfo = detailLine("最近速度：—")
        appsInfo = detailLine("应用检测：等待检测")
        candidateInfo = detailLine("候选版本更新时间：等待获取")
        detail.addView(candidateInfo); detail.addView(nodeInfo); detail.addView(speedInfo); detail.addView(appsInfo)
        body.addView(detail)

        val actions = card()
        actions.addView(label("操作",16,true,Color.rgb(37,49,66)))
        actions.addView(label("测速并发（最高 300）",13,false,Color.rgb(83,96,113)))
        workersSpinner = Spinner(this).apply {
            val choices = listOf("5","10","20","50","100","150","200","250","300")
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_item, choices).also {
                it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            }
            val saved = prefs.getInt("probe_workers", 200)
            setSelection(choices.indexOf(saved.toString()).takeIf { it >= 0 } ?: 6)
        }
        actions.addView(workersSpinner, LinearLayout.LayoutParams(-1, dp(48)))
        action(actions,"更新工作区","update")
        action(actions,"开始测速","probe")
        action(actions,"暂停测速","pause")
        action(actions,"继续测速","resume")
        action(actions,"停止测速","stop")
        actionButtons["pause"]?.visibility=View.GONE
        actionButtons["resume"]?.visibility=View.GONE
        actionButtons["stop"]?.visibility=View.GONE
        action(actions,"上传测速回执","upload")
        action(actions,"检查候选版本","check")
        body.addView(actions)

        logPanel = LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL
            background=rounded(Color.rgb(20,25,33),16)
            setPadding(dp(12),dp(10),dp(12),dp(10))
            layoutParams=LinearLayout.LayoutParams(-1,-2).apply { setMargins(0,dp(6),0,dp(12)) }
        }
        val terminalHeader = LinearLayout(this).apply {
            orientation=LinearLayout.HORIZONTAL
            gravity=android.view.Gravity.CENTER_VERTICAL
        }
        val terminalTitle = TextView(this).apply {
            text="●  TERMINAL"
            textSize=12f
            typeface=Typeface.DEFAULT_BOLD
            setTextColor(Color.rgb(205,218,232))
            letterSpacing=0.08f
        }
        terminalHeader.addView(terminalTitle,LinearLayout.LayoutParams(0,-2,1f))
        val liveLabel = TextView(this).apply {
            text="LIVE"
            textSize=10f
            typeface=Typeface.DEFAULT_BOLD
            setTextColor(Color.rgb(117,214,156))
            setPadding(dp(7),dp(4),dp(7),dp(4))
            background=rounded(Color.rgb(35,57,49),8)
        }
        terminalHeader.addView(liveLabel)
        val expandTerminal = TextView(this).apply {
            text=" ⤢ "
            textSize=20f
            setTextColor(Color.rgb(205,218,232))
            setPadding(dp(10),dp(2),dp(4),dp(2))
            contentDescription="展开或收起终端"
        }
        terminalHeader.addView(expandTerminal)
        logPanel.addView(terminalHeader)
        val terminalDivider = View(this).apply { setBackgroundColor(Color.rgb(52,59,70)) }
        logPanel.addView(terminalDivider,LinearLayout.LayoutParams(-1,dp(1)).apply { topMargin=dp(9); bottomMargin=dp(9) })
        logText = TextView(this).apply {
            text="等待 Termux 输出……"
            textSize=12f
            setTextColor(Color.rgb(205,218,232))
            typeface=Typeface.MONOSPACE
            maxLines=7
            setTextIsSelectable(true)
            setLineSpacing(dp(2).toFloat(),1f)
        }
        logPanel.addView(logText)
        val terminalFooter = LinearLayout(this).apply {
            orientation=LinearLayout.HORIZONTAL
            gravity=android.view.Gravity.CENTER_VERTICAL
            setPadding(0,dp(8),0,0)
        }
        val terminalHint = TextView(this).apply {
            text="~/AutoSubHub"
            textSize=10f
            setTextColor(Color.rgb(137,151,170))
        }
        terminalFooter.addView(terminalHint,LinearLayout.LayoutParams(0,-2,1f))
        val refreshTerminal = TextView(this).apply {
            text="↻ 刷新"
            textSize=11f
            setTextColor(Color.rgb(164,190,220))
            setPadding(dp(8),dp(5),dp(2),dp(5))
            setOnClickListener { runTermux("logs") }
        }
        terminalFooter.addView(refreshTerminal)
        logPanel.addView(terminalFooter)
        expandTerminal.setOnClickListener {
            val expanded = logText.maxLines <= 7
            logText.maxLines = if (expanded) 500 else 7
            expandTerminal.text = if (expanded) "⤡" else " ⤢ "
            terminalHint.text = if (expanded) "完整输出 · ~/AutoSubHub" else "~/AutoSubHub"
        }
        body.addView(logPanel)
        scroll.addView(body); root.addView(scroll); setContentView(root)
    }

    private fun card() = LinearLayout(this).apply {
        orientation=LinearLayout.VERTICAL; background=rounded(Color.WHITE,16); setPadding(dp(16),dp(15),dp(16),dp(15))
        layoutParams=LinearLayout.LayoutParams(-1,-2).apply { setMargins(0,dp(7),0,dp(7)) }
    }
    private fun label(t:String,size:Int,bold:Boolean,color:Int)=TextView(this).apply {
        text=t; textSize=size.toFloat(); setTextColor(color); if(bold) typeface=Typeface.DEFAULT_BOLD
        setPadding(0,dp(4),0,dp(5))
    }
    private fun detailLine(t:String)=label(t,13,false,Color.rgb(83,96,113))
    private fun stat(name:String,value:String):LinearLayout {
        val box=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; background=rounded(Color.WHITE,13); setPadding(dp(9),dp(10),dp(4),dp(10)) }
        val number=label(value,20,true,Color.rgb(28,45,68)).apply { tag="stat_"+name }; statViews[name]=number
        box.addView(number); box.addView(label(name,11,false,Color.GRAY))
        box.layoutParams=LinearLayout.LayoutParams(0,-2,1f).apply { setMargins(dp(3),dp(8),dp(3),dp(8)) }
        return box
    }
    private fun action(parent:LinearLayout,text:String,cmd:String) {
        val button = Button(this).apply {
            this.text=text
            isAllCaps=false
            setOnClickListener {
                var extraArgs = emptyArray<String>()
                if(cmd=="probe") {
                    val workers = workersSpinner.selectedItem.toString().toIntOrNull()?.coerceIn(1,300) ?: 200
                    prefs.edit().putInt("probe_workers", workers).apply()
                    extraArgs = arrayOf(workers.toString())
                    seenRunningThisSession=false
                    taskState="starting"
                    prefs.edit().putBoolean("probe_active",true).apply()
                    status.text="● 正在启动测速…"
                    progress.isIndeterminate=true
                    progressInfo.text="正在等待 Termux 启动结果…"
                    // 先监控任务生命周期；只有确认进程存在后才读取进度文件。
                    startPolling()
                }
                if(cmd=="pause") {
                    status.text="Ⅱ 正在请求暂停…"
                }
                if(cmd=="resume") {
                    status.text="● 正在继续测速…"
                    startPolling()
                }
                if(cmd=="stop") {
                    status.text="■ 正在停止：等待当前节点完成并保存结果…"
                    progress.isIndeterminate=false
                    actionButtons["pause"]?.visibility=View.GONE
                    actionButtons["resume"]?.visibility=View.GONE
                }
                runTermux(cmd, extraArgs=extraArgs)
            }
        }
        actionButtons[cmd]=button
        parent.addView(button,LinearLayout.LayoutParams(-1,-2))
    }

    private fun updateProgress(raw:String) {
        val a=raw.indexOf('{'); val b=raw.lastIndexOf('}')
        if(a<0||b<=a) return
        try {
            val d=JSONObject(raw.substring(a,b+1))
            val state=d.optString("status","idle")
            // While the process is alive, an old completed progress file must not overwrite its live state.
            if(taskState=="running" && (state=="completed"||state=="failed"||state=="interrupted")) return
            val activeTask=taskState=="running"
            val done=d.optInt("done",0); val total=d.optInt("total",0)
            val passed=d.optInt("passed",0); val failed=d.optInt("failed",0)
            val high=d.optInt("high",0); val normal=d.optInt("normal",0)
            val rate=d.optDouble("success_rate",0.0); val elapsed=d.optDouble("elapsed_seconds",0.0)
            val percent=if(total>0) done*100/total else 0
            progress.isIndeterminate=state=="preparing"; progress.progress=percent.coerceIn(0,100)
            progressInfo.text="已检测 "+done+" / "+total+"    ·    耗时 "+elapsed+" 秒"
            val stage=d.optString("stage","").trim()
            status.text=when(state) {
                "preparing"->"● 正在准备 · "+stage.ifEmpty { "获取候选节点" }
                "running"->"● 正在测速 · "+percent+"%"+(if(stage.isNotEmpty()) " · "+stage else "")
                "paused"->"Ⅱ 已暂停测速"
                "completed"->"✓ 测速完成"
                "interrupted"->"Ⅱ 测速已中断"
                "failed"->"! 测速失败"
                else->"● 等待开始"
            }
            setStat("已检测",done.toString()); setStat("可用",passed.toString())
            setStat("失败",failed.toString()); setStat("通过率",rate.toString()+"%")
            val candidateUpdated=d.optString("candidate_updated_at","")
            val candidateFetched=d.optString("candidate_fetched_at","")
            candidateInfo.text=if(candidateUpdated.isNotEmpty()) "候选更新时间（北京时间）："+candidateUpdated+"\n本机获取时间："+(candidateFetched.ifEmpty { "—" }) else "候选版本更新时间：等待获取"
            nodeInfo.text="最近完成："+d.optString("current_node","—")
            speedInfo.text="最近速度："+d.optDouble("latest_speed_kbps",0.0)+" KB/s  ·  高速 "+high+" / 普通 "+normal
            val checks=d.optJSONObject("app_checks")
            appsInfo.text="应用检测："+if(checks==null||checks.length()==0) "等待检测" else
                listOf("youtube","telegram","google","chatgpt","github").joinToString("  ") { key ->
                    val ok=checks.optJSONObject(key)?.optString("status")=="passed"
                    serviceName(key)+(if(ok) " ✓" else " ×")
                }
            actionButtons["pause"]?.visibility=if(activeTask && (state=="running"||state=="preparing")) View.VISIBLE else View.GONE
            actionButtons["resume"]?.visibility=if(activeTask && state=="paused") View.VISIBLE else View.GONE
            actionButtons["stop"]?.visibility=if(activeTask) View.VISIBLE else View.GONE
            actionButtons["probe"]?.visibility=if(activeTask) View.GONE else View.VISIBLE
            if(state=="completed"||state=="interrupted"||state=="failed") {
                prefs.edit().putBoolean("probe_active",false).apply()
            }
        } catch (_:Exception) {}
    }
    private fun setStat(name:String,value:String) { statViews[name]?.text=value }
    private fun serviceName(k:String)=when(k) { "youtube"->"YouTube"; "telegram"->"TG"; "google"->"Google"; "chatgpt"->"AI"; else->"GitHub" }
    private fun updateTaskStatus(raw:String) {
        val a=raw.indexOf('{'); val b=raw.lastIndexOf('}')
        if(a<0||b<=a) return
        try {
            val d=JSONObject(raw.substring(a,b+1))
            val state=d.optString("status","idle")
            taskState=state
            when(state) {
                "running" -> {
                    seenRunningThisSession=true
                    prefs.edit().putBoolean("probe_active",true).apply()
                    status.text="● 已检测到运行中的测速进程 · PID "+d.optInt("pid",0)
                    progress.isIndeterminate=false
                    runTermux("progress",false)
                }
                "starting" -> {
                    prefs.edit().putBoolean("probe_active",true).apply()
                    status.text="● 正在启动测速任务…"
                    progress.isIndeterminate=true
                    progressInfo.text="正在等待测速进程启动…"
                    actionButtons["probe"]?.visibility=View.GONE
                }
                "interrupted" -> {
                    monitoringTask=false
                    prefs.edit().putBoolean("probe_active",false).apply()
                    status.text="Ⅱ 上一次测速异常中断"
                    progress.isIndeterminate=false
                    actionButtons["probe"]?.visibility=View.VISIBLE
                    actionButtons["pause"]?.visibility=View.GONE
                    actionButtons["resume"]?.visibility=View.GONE
                    actionButtons["stop"]?.visibility=View.GONE
                }
                else -> {
                    monitoringTask=false
                    prefs.edit().putBoolean("probe_active",false).apply()
                    if(seenRunningThisSession) runTermux("progress",false)
                    else status.text="○ 当前没有运行中的测速任务 · 历史记录保留"
                    progress.isIndeterminate=false
                    actionButtons["probe"]?.visibility=View.VISIBLE
                    actionButtons["pause"]?.visibility=View.GONE
                    actionButtons["resume"]?.visibility=View.GONE
                    actionButtons["stop"]?.visibility=View.GONE
                }
            }
        } catch (_:Exception) {}
    }
    private fun startPolling(){ monitoringTask=true; handler.removeCallbacks(poll); handler.post(poll) }

    private fun runTermux(action:String,showFeedback:Boolean=true,extraArgs:Array<String> = emptyArray()) {
        if(Build.VERSION.SDK_INT>=23 && checkSelfPermission("com.termux.permission.RUN_COMMAND")!=PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this,"请先授予 Termux 命令权限",Toast.LENGTH_LONG).show()
            requestPermissions(arrayOf("com.termux.permission.RUN_COMMAND"),41); return
        }
        val intent=Intent("com.termux.RUN_COMMAND").apply {
            setClassName("com.termux","com.termux.app.RunCommandService")
            putExtra("com.termux.RUN_COMMAND_PATH","/data/data/com.termux/files/usr/bin/bash")
            putExtra("com.termux.RUN_COMMAND_ARGUMENTS",arrayOf("/data/data/com.termux/files/home/AutoSubHub/app_control.sh",action)+extraArgs)
            putExtra("com.termux.RUN_COMMAND_WORKDIR","/data/data/com.termux/files/home/AutoSubHub")
            putExtra("com.termux.RUN_COMMAND_BACKGROUND",true)
            putExtra("com.termux.RUN_COMMAND_SESSION_ACTION","3")
        }
        val callback=Intent(this,CommandResultReceiver::class.java).apply {
            putExtra("command",action); setAction("com.ccvvv.autosubhub.RESULT."+action+"."+System.currentTimeMillis())
        }
        val flags=PendingIntent.FLAG_ONE_SHOT or if(Build.VERSION.SDK_INT>=31) PendingIntent.FLAG_MUTABLE else 0
        intent.putExtra("com.termux.RUN_COMMAND_PENDING_INTENT",PendingIntent.getBroadcast(this,nextId.incrementAndGet(),callback,flags))
        try { startService(intent); if(showFeedback&&action!="progress") logText.text="已发送命令："+action+"\n等待 Termux 返回结果……" }
        catch(e:Exception){ logText.text="调用 Termux 失败："+e.message+"\n请确认 Termux 已安装并开启 allow-external-apps。" }
    }
    private fun rounded(color:Int,radius:Int)=GradientDrawable().apply { setColor(color); cornerRadius=dp(radius).toFloat() }
    private fun dp(v:Int)=(v*resources.displayMetrics.density).toInt()
    companion object { private val nextId=AtomicInteger(100) }
}
