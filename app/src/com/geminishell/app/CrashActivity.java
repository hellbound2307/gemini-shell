package com.geminishell.app;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.graphics.Color;
import android.os.Bundle;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;

/**
 * Terminal surface for uncaught exceptions.
 *
 * Why this exists: on this device there is no logcat (Shizuku down), no
 * readable /data, and an AccessibilityService can only see the foreground
 * window. Every previous diagnosis of "the app exits immediately" was a
 * GUESS, because the actual stack trace was never observable.
 *
 * So the app captures its own death and puts it somewhere the operator (and
 * the agent, via the clipboard) can actually read:
 *
 *   1. on-screen, full screen, monospace, scrollable  - screenshot-able
 *   2. the system clipboard                            - pasteable into chat
 *   3. getExternalFilesDir(null)/crash-<n>.txt         - survives the process
 *
 * This is diagnostic scaffolding. It never runs on a healthy launch.
 */
public class CrashActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        String trace = getIntent() != null
                ? getIntent().getStringExtra("trace")
                : null;
        if (trace == null || trace.length() == 0) {
            trace = "CrashActivity opened with no trace payload.\n"
                    + "extras=" + (getIntent() == null
                    ? "null intent"
                    : String.valueOf(getIntent().getExtras()));
        }

        persist(trace);
        copyToClipboard(trace);

        TextView tv = new TextView(this);
        tv.setText(trace);
        tv.setTextColor(Color.parseColor("#FF6B6B"));
        tv.setBackgroundColor(Color.parseColor("#0B0C0D"));
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        tv.setTypeface(android.graphics.Typeface.MONOSPACE);
        tv.setPadding(24, 24, 24, 24);
        tv.setTextIsSelectable(true);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(tv);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#0B0C0D"));
        root.setGravity(Gravity.FILL);

        TextView banner = new TextView(this);
        banner.setText("GEMINI SHELL CRASH — trace copied to clipboard");
        banner.setTextColor(Color.parseColor("#C8F751"));
        banner.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        banner.setTypeface(android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD);
        banner.setPadding(24, 32, 24, 16);

        root.addView(banner);
        root.addView(scroll);
        setContentView(root);
    }

    /** Best-effort: never let diagnostics themselves throw. */
    private void persist(String trace) {
        try {
            File dir = getExternalFilesDir(null);
            if (dir == null) return;
            if (!dir.exists() && !dir.mkdirs()) return;
            File f = new File(dir, "crash.txt");
            FileOutputStream fos = new FileOutputStream(f, false);
            try {
                fos.write(trace.getBytes("UTF-8"));
            } finally {
                fos.close();
            }
        } catch (Throwable ignored) {
        }
    }

    private void copyToClipboard(String trace) {
        try {
            ClipboardManager cm =
                    (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm == null) return;
            cm.setPrimaryClip(ClipData.newPlainText("gemini-shell-crash", trace));
        } catch (Throwable ignored) {
        }
    }

    /** Renders any Throwable, including its full cause chain. */
    public static String render(Throwable t) {
        if (t == null) return "(no throwable)";
        StringWriter sw = new StringWriter();
        try {
            PrintWriter pw = new PrintWriter(sw);
            t.printStackTrace(pw);
            pw.flush();
        } catch (Throwable ignored) {
            return "trace rendering failed: " + t;
        }
        String s = sw.toString();
        return "time=" + System.currentTimeMillis()
                + "\nandroid=" + android.os.Build.VERSION.SDK_INT
                + " (" + android.os.Build.VERSION.RELEASE + ")\n"
                + "device=" + android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL
                + "\n----\n" + s;
    }
}
