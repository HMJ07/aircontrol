package com.aircontrol.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {
    private lateinit var status: TextView
    private lateinit var start: Button
    private lateinit var stop: Button
    private lateinit var pause: Button

    private val askCamera = registerForActivityResult(ActivityResultContracts.RequestPermission()) { refresh() }
    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { refresh() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad)
        }
        fun text(t: String, size: Float, bold: Boolean = false) = TextView(this).apply {
            this.text = t; textSize = size; if (bold) setTypeface(typeface, Typeface.BOLD)
            setPadding(0, pad / 2, 0, pad / 2)
        }
        fun button(t: String, onClick: () -> Unit) = Button(this).apply { text = t; setOnClickListener { onClick() } }

        root.addView(text("AirControl", 26f, true))
        root.addView(text("Controla este teléfono con la mano que ve la cámara frontal. Todo se procesa aquí: no se envía ni se guarda nada.", 15f))
        status = text("", 16f)
        root.addView(status)
        root.addView(button("1. Conceder la cámara") { askCamera.launch(Manifest.permission.CAMERA) })
        root.addView(button("2. Activar AirControl en Accesibilidad") { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) })
        root.addView(button("Si Android bloquea la activación: Ajustes restringidos") {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        })
        start = button("Iniciar control") { startControl() }
        stop = button("Detener control") { stopControl() }
        pause = button("Pausar / reanudar") { ControlService.command("toggle_pause") }
        root.addView(start); root.addView(pause); root.addView(stop)
        root.addView(text(
            "Cómo se usa: señala con el índice para mover el cursor; pellizca pulgar + índice para tocar (dos veces = doble toque; " +
                "mantener = arrastrar); pulgar + corazón = pulsación larga; ✌️ y mover la mano = desplazar. " +
                "Palma abierta deslizándose: izquierda = Atrás, derecha = Recientes, arriba = Inicio, abajo = Notificaciones. " +
                "Puño cerrado 1,2 s = pausa.", 14f
        ))
        root.addView(text(
            "En Android 13 o superior, al instalar el APK fuera de Google Play el sistema bloquea el servicio de Accesibilidad: " +
                "pulsa «Ajustes restringidos», abre ⋮ (arriba a la derecha) → «Permitir ajustes restringidos» y vuelve a activarlo.", 13f
        ))
        setContentView(ScrollView(this).apply { addView(root) })
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun cameraGranted() = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    private fun accessibilityEnabled(): Boolean {
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
        val full = "$packageName/${AirAccessibilityService::class.java.name}"
        val short = "$packageName/.AirAccessibilityService"
        return enabled.split(':').any { it.equals(full, true) || it.equals(short, true) }
    }

    private fun refresh() {
        val cam = cameraGranted()
        val acc = accessibilityEnabled()
        val running = ControlService.running
        status.text = buildString {
            append(if (cam) "✅ Cámara concedida\n" else "❌ Falta el permiso de la cámara\n")
            append(if (acc) "✅ Accesibilidad activada\n" else "❌ Falta activar AirControl en Accesibilidad\n")
            append(if (running) "▶ Control en marcha" else "⏸ Control detenido")
        }
        start.isEnabled = cam && acc && !running
        stop.isEnabled = running
        pause.visibility = if (running) View.VISIBLE else View.GONE
    }

    private fun startControl() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)       // sin él el servicio funciona, pero sin aviso visible
        ContextCompat.startForegroundService(this, Intent(this, ControlService::class.java))
        status.postDelayed({ refresh() }, 1500)
    }

    private fun stopControl() {
        startService(Intent(this, ControlService::class.java).setAction(ControlService.ACTION_STOP))
        status.postDelayed({ refresh() }, 800)
    }
}
