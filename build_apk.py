"""
PC Remote - Native Android APK Builder
Compiles a standalone, high-performance Android APK using OpenJDK 17 and Android SDK 34:
- Hardware accelerated WebView
- Offline HTTPS origin interception (https://pcremote.local)
- Cleartext traffic enabled for LAN HTTP communication
- Native AndroidBridge for haptics and toast alerts
- Signed with debug keystore and zipalign optimized
"""

import os
import sys
import shutil
import subprocess
import zipfile
from pathlib import Path
from PIL import Image

# Ensure UTF-8 output on Windows terminal
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Paths setup
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR / "android"
MOBILE_APP_DIR = SCRIPT_DIR / "mobile_app"
DIST_DIR = PROJECT_DIR / "dist"
DESKTOP_DIR = Path(r"C:\Users\dev\Desktop")

JAVA_HOME = Path(r"C:\Users\dev\android-env\jdk-17.0.12+7")
ANDROID_HOME = Path(r"C:\Users\dev\android-env\android-sdk")
BUILD_TOOLS = ANDROID_HOME / "build-tools" / "34.0.0"
PLATFORM_JAR = ANDROID_HOME / "platforms" / "android-34" / "android.jar"

AAPT2 = BUILD_TOOLS / "aapt2.exe"
D8 = BUILD_TOOLS / "d8.bat"
ZIPALIGN = BUILD_TOOLS / "zipalign.exe"
APKSIGNER = BUILD_TOOLS / "apksigner.bat"

JAVAC = JAVA_HOME / "bin" / "javac.exe"
KEYTOOL = JAVA_HOME / "bin" / "keytool.exe"
KEYSTORE_PATH = PROJECT_DIR / "debug.keystore"

APP_CONFIG = {
    "app_name": "PC Remote",
    "package_name": "com.antigravity.pcremote",
    "host": "pcremote.local",
    "output_apk": "PCRemote.apk",
    "bg_color": "#08090d",
}

def verify_env():
    print("🔍 Verifying SDK & JDK toolchain...")
    tools = [
        ("Java compiler", JAVAC),
        ("Keytool", KEYTOOL),
        ("Android jar", PLATFORM_JAR),
        ("aapt2", AAPT2),
        ("d8", D8),
        ("zipalign", ZIPALIGN),
        ("apksigner", APKSIGNER),
    ]
    for name, path in tools:
        if not path.is_file():
            raise FileNotFoundError(f"Missing required tool: {name} at {path}")
    print("✅ Toolchain verified!")

def ensure_keystore(env):
    if not KEYSTORE_PATH.exists():
        print("🔑 Generating Android debug signing keystore...")
        cmd_keytool = [
            str(KEYTOOL),
            "-genkeypair", "-v",
            "-keystore", str(KEYSTORE_PATH),
            "-storepass", "android",
            "-alias", "androiddebugkey",
            "-keypass", "android",
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-dname", "CN=PCRemote,O=Antigravity,C=US"
        ]
        subprocess.check_call(cmd_keytool, env=env)
        print("✅ Keystore created.")

def prepare_project():
    print(f"\n=======================================================")
    print(f"📦 PREPARING PROJECT: {APP_CONFIG['app_name']}")
    print(f"=======================================================")

    build_dir = PROJECT_DIR / "build"
    src_dir = PROJECT_DIR / "src" / "com" / "antigravity" / "pcremote"
    res_dir = PROJECT_DIR / "res"
    values_dir = res_dir / "values"
    assets_dir = PROJECT_DIR / "assets"

    # Clean previous build & src
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "classes").mkdir(parents=True, exist_ok=True)
    (build_dir / "dex").mkdir(parents=True, exist_ok=True)

    if (PROJECT_DIR / "src").exists():
        shutil.rmtree(PROJECT_DIR / "src")
    src_dir.mkdir(parents=True, exist_ok=True)

    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    values_dir.mkdir(parents=True, exist_ok=True)

    # 1. AndroidManifest.xml
    manifest_content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{APP_CONFIG['package_name']}"
    android:versionCode="1"
    android:versionName="1.0.0">

    <uses-sdk
        android:minSdkVersion="24"
        android:targetSdkVersion="34" />

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.VIBRATE" />

    <application
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:roundIcon="@mipmap/ic_launcher"
        android:allowBackup="true"
        android:supportsRtl="true"
        android:hardwareAccelerated="true"
        android:usesCleartextTraffic="true"
        android:theme="@style/AppTheme">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:label="@string/app_name"
            android:configChanges="orientation|screenSize|screenLayout|keyboardHidden"
            android:windowSoftInputMode="adjustResize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
    (PROJECT_DIR / "AndroidManifest.xml").write_text(manifest_content, encoding="utf-8")

    # 2. strings.xml
    strings_content = f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">{APP_CONFIG['app_name']}</string>
</resources>
"""
    (values_dir / "strings.xml").write_text(strings_content, encoding="utf-8")

    # 3. styles.xml
    styles_content = f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="AppTheme" parent="@android:style/Theme.DeviceDefault.NoActionBar">
        <item name="android:windowBackground">{APP_CONFIG['bg_color']}</item>
        <item name="android:statusBarColor">{APP_CONFIG['bg_color']}</item>
        <item name="android:navigationBarColor">{APP_CONFIG['bg_color']}</item>
    </style>
</resources>
"""
    (values_dir / "styles.xml").write_text(styles_content, encoding="utf-8")

    # 4. Generate multi-DPI launcher icons from mobile_app/icon-512.png
    src_icon = MOBILE_APP_DIR / "icon-512.png"
    icon_img = Image.open(src_icon).convert("RGBA")
    densities = {
        "mipmap-mdpi": (48, 48),
        "mipmap-hdpi": (72, 72),
        "mipmap-xhdpi": (96, 96),
        "mipmap-xxhdpi": (144, 144),
        "mipmap-xxxhdpi": (192, 192),
    }
    for folder_name, size in densities.items():
        folder_path = res_dir / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        resized = icon_img.resize(size, Image.Resampling.LANCZOS)
        resized.save(folder_path / "ic_launcher.png", format="PNG")

    # 5. MainActivity.java
    java_content = f"""package com.antigravity.pcremote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;
import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

public class MainActivity extends Activity {{
    private WebView webView;
    private static final String APP_HOST = "{APP_CONFIG['host']}";
    private static final String APP_URL = "https://" + APP_HOST + "/index.html";

    public class AndroidBridge {{
        private final Context context;

        AndroidBridge(Context ctx) {{
            this.context = ctx;
        }}

        @JavascriptInterface
        public void showToast(final String msg) {{
            runOnUiThread(new Runnable() {{
                @Override
                public void run() {{
                    Toast.makeText(context, msg, Toast.LENGTH_SHORT).show();
                }}
            }});
        }}

        @JavascriptInterface
        public void vibrate(final long ms) {{
            try {{
                Vibrator v = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
                if (v != null && v.hasVibrator()) {{
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {{
                        v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                    }} else {{
                        v.vibrate(ms);
                    }}
                }}
            }} catch (Exception ignored) {{}}
        }}

        @JavascriptInterface
        public boolean sendWakeOnLan(final String macStr) {{
            try {{
                String cleanMac = (macStr == null ? "A83B7676208F" : macStr).replaceAll("[^0-9A-Fa-f]", "");
                if (cleanMac.length() != 12) return false;
                final byte[] macBytes = new byte[6];
                for (int i = 0; i < 6; i++) {{
                    macBytes[i] = (byte) Integer.parseInt(cleanMac.substring(i * 2, i * 2 + 2), 16);
                }}
                final byte[] bytes = new byte[6 + 16 * 6];
                for (int i = 0; i < 6; i++) {{
                    bytes[i] = (byte) 0xff;
                }}
                for (int i = 6; i < bytes.length; i += 6) {{
                    System.arraycopy(macBytes, 0, bytes, i, 6);
                }}
                new Thread(new Runnable() {{
                    @Override
                    public void run() {{
                        try {{
                            java.net.InetAddress addr = java.net.InetAddress.getByName("255.255.255.255");
                            java.net.DatagramPacket packet = new java.net.DatagramPacket(bytes, bytes.length, addr, 9);
                            java.net.DatagramSocket socket = new java.net.DatagramSocket();
                            socket.setBroadcast(true);
                            socket.send(packet);
                            socket.close();
                        }} catch (Exception ignored) {{}}
                    }}
                }}).start();
                return true;
            }} catch (Exception e) {{
                return false;
            }}
        }}
    }}

    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);

        // Immersive dark status bar
        Window window = getWindow();
        window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS);
        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        window.setStatusBarColor(0xFF08090D);
        window.setNavigationBarColor(0xFF08090D);

        webView = new WebView(this);
        webView.setBackgroundColor(0xFF08090D);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.addJavascriptInterface(new AndroidBridge(this), "AndroidBridge");

        webView.setWebChromeClient(new WebChromeClient() {{
            @Override
            public boolean onJsAlert(WebView view, String url, String message, final JsResult result) {{
                new AlertDialog.Builder(MainActivity.this)
                    .setTitle("{APP_CONFIG['app_name']}")
                    .setMessage(message)
                    .setPositiveButton(android.R.string.ok, new DialogInterface.OnClickListener() {{
                        @Override
                        public void onClick(DialogInterface dialog, int which) {{
                            result.confirm();
                        }}
                    }})
                    .setCancelable(false)
                    .show();
                return true;
            }}

            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {{
                Log.d("PCRemote", cm.message() + " [L:" + cm.lineNumber() + "]");
                return true;
            }}
        }});

        // Intercept local https://pcremote.local/ requests from packaged assets
        webView.setWebViewClient(new WebViewClient() {{
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {{
                Uri uri = request.getUrl();
                if (APP_HOST.equalsIgnoreCase(uri.getHost())) {{
                    String path = uri.getPath();
                    if (path == null || path.equals("/") || path.isEmpty()) {{
                        path = "/index.html";
                    }}
                    if (path.startsWith("/")) {{
                        path = path.substring(1);
                    }}
                    try {{
                        InputStream stream = getAssets().open(path);
                        String mimeType = getMimeType(path);
                        Map<String, String> headers = new HashMap<String, String>();
                        headers.put("Access-Control-Allow-Origin", "*");
                        return new WebResourceResponse(mimeType, "UTF-8", 200, "OK", headers, stream);
                    }} catch (IOException e) {{
                        return null;
                    }}
                }}
                return super.shouldInterceptRequest(view, request);
            }}

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {{
                Uri uri = request.getUrl();
                if (APP_HOST.equalsIgnoreCase(uri.getHost())) {{
                    return false;
                }}
                return false;
            }}
        }});

        setContentView(webView);
        webView.loadUrl(APP_URL);
    }}

    private String getMimeType(String path) {{
        if (path.endsWith(".html")) return "text/html";
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".json")) return "application/json";
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".svg")) return "image/svg+xml";
        return "application/octet-stream";
    }}

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {{
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {{
            webView.goBack();
            return true;
        }}
        return super.onKeyDown(keyCode, event);
    }}
}}
"""
    (src_dir / "MainActivity.java").write_text(java_content, encoding="utf-8")

    # 6. Copy web assets to assets/
    print(f"📦 Bundling mobile web assets into assets/ ...")
    for item in MOBILE_APP_DIR.iterdir():
        if item.is_file():
            shutil.copy2(item, assets_dir / item.name)
            print(f"   -> assets/{item.name} ({item.stat().st_size} bytes)")

    print(f"✅ Project files and assets prepared for {APP_CONFIG['app_name']}!")

def build_app(env):
    build_dir = PROJECT_DIR / "build"
    compiled_res_zip = build_dir / "compiled_res.zip"
    base_apk = build_dir / "base.apk"
    unaligned_apk = build_dir / "unaligned.apk"
    aligned_apk = build_dir / "aligned.apk"
    
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    final_apk = DIST_DIR / APP_CONFIG["output_apk"]

    # Step 1: AAPT2 Compile Resources
    print(f"\n⚙️ [1/6] Compiling Android resources with aapt2...")
    cmd_compile = [
        str(AAPT2),
        "compile",
        "--dir", str(PROJECT_DIR / "res"),
        "-o", str(compiled_res_zip)
    ]
    subprocess.check_call(cmd_compile, env=env)

    # Step 2: AAPT2 Link Resources & Generate R.java
    print(f"⚙️ [2/6] Linking resources & generating R.java with aapt2...")
    cmd_link = [
        str(AAPT2),
        "link",
        "-I", str(PLATFORM_JAR),
        "--manifest", str(PROJECT_DIR / "AndroidManifest.xml"),
        str(compiled_res_zip),
        "-o", str(base_apk),
        "--java", str(PROJECT_DIR / "src"),
        "--auto-add-overlay"
    ]
    subprocess.check_call(cmd_link, env=env)

    # Step 3: Compile Java source files
    print(f"⚙️ [3/6] Compiling Java sources with javac...")
    src_app_dir = PROJECT_DIR / "src" / "com" / "antigravity" / "pcremote"
    java_files = [
        str(src_app_dir / "R.java"),
        str(src_app_dir / "MainActivity.java")
    ]
    cmd_javac = [
        str(JAVAC),
        "-encoding", "UTF-8",
        "-source", "17",
        "-target", "17",
        "-cp", str(PLATFORM_JAR),
        "-d", str(build_dir / "classes")
    ] + java_files
    subprocess.check_call(cmd_javac, env=env)

    # Step 4: D8 Dexing
    print(f"⚙️ [4/6] Converting bytecode to classes.dex with d8...")
    class_files = list((build_dir / "classes" / "com" / "antigravity" / "pcremote").glob("*.class"))
    cmd_d8 = [
        "cmd.exe", "/c", str(D8),
        "--lib", str(PLATFORM_JAR),
        "--output", str(build_dir / "dex")
    ] + [str(c) for c in class_files]
    subprocess.check_call(cmd_d8, env=env)

    dex_file = build_dir / "dex" / "classes.dex"
    if not dex_file.exists():
        raise FileNotFoundError("classes.dex was not generated by d8!")

    # Step 5: Assemble Unaligned APK with classes.dex & assets
    print(f"⚙️ [5/6] Assembling APK package with assets & classes.dex...")
    shutil.copy2(base_apk, unaligned_apk)

    with zipfile.ZipFile(unaligned_apk, "a") as zf:
        zf.write(dex_file, arcname="classes.dex")
        assets_dir = PROJECT_DIR / "assets"
        for asset in assets_dir.rglob("*"):
            if asset.is_file():
                rel_path = asset.relative_to(assets_dir)
                zf.write(asset, arcname=f"assets/{rel_path.as_posix()}")

    # Step 6: Zipalign & Sign APK
    print(f"⚙️ [6/6] Aligning & Signing APK with apksigner...")
    if aligned_apk.exists():
        aligned_apk.unlink()

    cmd_zipalign = [
        str(ZIPALIGN),
        "-p", "-f", "4",
        str(unaligned_apk),
        str(aligned_apk)
    ]
    subprocess.check_call(cmd_zipalign, env=env)

    if final_apk.exists():
        final_apk.unlink()
    idsig_file = Path(str(final_apk) + ".idsig")
    if idsig_file.exists():
        idsig_file.unlink()

    cmd_sign = [
        "cmd.exe", "/c", str(APKSIGNER),
        "sign",
        "--ks", str(KEYSTORE_PATH),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--out", str(final_apk),
        str(aligned_apk)
    ]
    subprocess.check_call(cmd_sign, env=env)

    # Verify signature
    cmd_verify = [
        "cmd.exe", "/c", str(APKSIGNER),
        "verify",
        "--verbose",
        str(final_apk)
    ]
    subprocess.check_call(cmd_verify, env=env)

    # Copy to Desktop
    desktop_apk = DESKTOP_DIR / APP_CONFIG["output_apk"]
    shutil.copy2(final_apk, desktop_apk)

    apk_size_kb = final_apk.stat().st_size / 1024
    print("\n" + "=" * 60)
    print(f"🎉 APK BUILD SUCCESSFUL!")
    print(f"📦 Dist APK:    {final_apk} ({apk_size_kb:.1f} KB)")
    print(f"🖥️ Desktop APK: {desktop_apk}")
    print("=" * 60)

def main():
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["ANDROID_HOME"] = str(ANDROID_HOME)
    env["PATH"] = f"{JAVA_HOME / 'bin'};{BUILD_TOOLS};{env.get('PATH', '')}"

    verify_env()
    ensure_keystore(env)
    prepare_project()
    build_app(env)

if __name__ == "__main__":
    main()
