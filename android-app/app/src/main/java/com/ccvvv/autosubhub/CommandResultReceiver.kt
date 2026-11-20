package com.ccvvv.autosubhub

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class CommandResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val bundle = intent.getBundleExtra("result")
        val stdout = bundle?.getString("stdout").orEmpty()
        val stderr = bundle?.getString("stderr").orEmpty()
        val exitCode = bundle?.getInt("exitCode", -1) ?: -1
        val errMsg = bundle?.getString("errmsg").orEmpty()
        val command = intent.getStringExtra("command").orEmpty()
        val text = buildString {
            append("[$command] exit=$exitCode\n")
            if (stdout.isNotBlank()) append(stdout)
            if (stderr.isNotBlank()) append("\n[stderr]\n$stderr")
            if (errMsg.isNotBlank()) append("\n$errMsg")
        }
        context.getSharedPreferences("console", Context.MODE_PRIVATE)
            .edit()
            .putString("last_result", text)
            .putString("last_command", command)
            .apply()
    }
}
