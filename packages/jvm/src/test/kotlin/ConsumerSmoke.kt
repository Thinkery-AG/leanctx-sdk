package com.thinkery.leanctx.consumer

import com.thinkery.leanctx.AgentPermissions
import com.thinkery.leanctx.ContextPlan
import com.thinkery.leanctx.ContextSource
import com.thinkery.leanctx.ExecutionPolicy
import com.thinkery.leanctx.ReadMode

fun consumerSmoke(): String {
    val source = ContextSource("src/Main.kt", "/PROJECT")
    val plan = ContextPlan("session", "task", "inspect", source)
    val permissions = AgentPermissions()
    val policy = ExecutionPolicy(30.0, listOf("java"), listOf("LANG"))
    return "${plan.mode()}-${ReadMode.AUTO.value()}-${permissions.allowWrite()}-${policy.maxTimeout()}"
}
