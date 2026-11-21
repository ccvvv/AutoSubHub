package com.ccvvv.autosubhub
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class CommandResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val b=intent.getBundleExtra("result")
        val stdout=b?.getString("stdout").orEmpty()
        val stderr=b?.getString("stderr").orEmpty()
        val code=b?.getInt("exitCode",-1) ?: -1
        val err=b?.getString("errmsg").orEmpty()
        val command=intent.getStringExtra("command").orEmpty()
        val output=buildString {
            if(stdout.isNotBlank()) append(stdout)
            if(stderr.isNotBlank()) append("\n[stderr]\n"+stderr)
            if(err.isNotBlank()) append("\n"+err)
            if(isBlank()) append("exit="+code)
        }
        context.getSharedPreferences("console",Context.MODE_PRIVATE).edit()
            .putString("last_command",command)
            .putString("last_result",if(command=="progress") output else "["+command+"] exit="+code+"\n"+output)
            .apply()
    }
}
