package com.geminishell.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * Routes uncaught exceptions to {@link CrashActivity} so the failure is
 * observable on a device with no logcat access.
 *
 * Installed as the default uncaught-exception handler, which also covers
 * failures on the main thread AFTER onCreate returns (a Custom Tabs callback,
 * a resource inflation, a late class load) — not just synchronous throws.
 */
final class CrashTrap {

    private static Thread.UncaughtExceptionHandler previous;
    private static boolean installed;

    private CrashTrap() {
    }

    static synchronized void install(Context ctx) {
        if (installed) return;
        installed = true;

        previous = Thread.getDefaultUncaughtExceptionHandler();

        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            @Override
            public void uncaughtException(Thread t, Throwable e) {
                try {
                    show(ctx, e);
                } catch (Throwable ignored) {
                    // Diagnostics must never mask the original failure.
                }
                // Give the crash activity a moment to render before the
                // process is torn down, then honour the default behaviour.
                try {
                    Thread.sleep(400L);
                } catch (InterruptedException ignored) {
                }
                if (previous != null) {
                    previous.uncaughtException(t, e);
                } else {
                    System.exit(2);
                }
            }
        });
    }

    /** Show the trace synchronously (used for try/catch call sites). */
    static void report(Activity act, Throwable t) {
        show(act, t);
    }

    private static void show(Context ctx, Throwable t) {
        Intent i = new Intent(ctx, CrashActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_CLEAR_TASK
                | Intent.FLAG_ACTIVITY_NO_ANIMATION);
        i.putExtra("trace", CrashActivity.render(t));
        ctx.startActivity(i);
    }
}
