# Mobile App Build & Development Guide (Flutter)

**Target Applications:** Smart Attendance Student & Teacher Companion Apps  
**Supported Platforms:** Android (minSdkVersion 24 / Android 7.0+) & iOS (iOS 14.0+)  
**Framework Version:** Flutter 3.22+ / Dart 3.4+  
**Backend API Version:** `v1` (`/api/`)

---

## 1. Prerequisites & Environment Setup

### System Requirements
* **Flutter SDK:** `>= 3.22.0` (Install via `fvm` or official installer)
* **Dart SDK:** `>= 3.4.0`
* **Android Studio / Android SDK:** Android SDK Command-line Tools, Build-Tools 34.0.0+, Android Platform 34
* **Xcode (macOS only):** Xcode 15+, CocoaPods (`sudo gem install cocoapods`)
* **VS Code / Android Studio Plugins:** Flutter & Dart extensions

Verify setup with:
```bash
flutter doctor -v
```

---

## 2. Project Initialization & Dependencies

### A. Create Project
```bash
flutter create \
  --org com.school.attendance \
  --project-name smart_attendance \
  --platforms=android,ios \
  smart_attendance_mobile

cd smart_attendance_mobile
```

### B. `pubspec.yaml` Recommended Dependencies

Add the following production-grade packages to `pubspec.yaml`:

```yaml
name: smart_attendance
description: "Smart Attendance Mobile App for Students and Teachers"
publish_to: "none"
version: 1.0.0+1

environment:
  sdk: ">=3.4.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter

  # Networking & Serialization
  dio: ^5.4.3+1
  json_annotation: ^4.9.0

  # Secure Storage & Session
  flutter_secure_storage: ^9.2.2
  shared_preferences: ^2.2.3

  # State Management (Choose Riverpod or BLoC; Riverpod recommended)
  flutter_riverpod: ^2.5.1

  # Camera & Biometric Hardware
  camera: ^0.10.6
  mobile_scanner: ^5.1.1 # High-performance Barcode/QR scanner

  # UI, Icons & Polish
  flutter_svg: ^2.0.10+1
  google_fonts: ^6.2.1
  intl: ^0.19.0
  flutter_animate: ^4.5.0
  top_snackbar_flutter: ^3.1.0

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^4.0.0
  build_runner: ^2.4.9
  json_serializable: ^6.8.0
```

Install packages:
```bash
flutter pub get
```

---

## 3. Platform Configuration & Hardware Permissions

### A. Android Configuration

#### 1. Manifest Permissions (`android/app/src/main/AndroidManifest.xml`)
Inside the `<manifest>` tag, above `<application>`:
```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <!-- Internet & Network Monitoring -->
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>

    <!-- Camera for QR Scanning & Selfie Enrollment -->
    <uses-permission android:name="android.permission.CAMERA"/>
    <uses-feature android:name="android.hardware.camera" android:required="true"/>
    <uses-feature android:name="android.hardware.camera.autofocus" android:required="false"/>

    <application
        android:label="Smart Attendance"
        android:name="${applicationName}"
        android:icon="@mipmap/ic_launcher"
        android:usesCleartextTraffic="true"> <!-- Required for local HTTP dev testing -->
        ...
    </application>
</manifest>
```

#### 2. Min SDK Version (`android/app/build.gradle`)
Ensure `minSdkVersion` is at least 24:
```groovy
defaultConfig {
    applicationId "com.school.attendance"
    minSdkVersion 24
    targetSdkVersion 34
    versionCode flutterVersionCode.toInteger()
    versionName flutterVersionName
}
```

---

### B. iOS Configuration

#### 1. Privacy Permissions (`ios/Runner/Info.plist`)
Inside `<dict>`:
```xml
<!-- Camera Access for QR Code & Face Enrollment -->
<key>NSCameraUsageDescription</key>
<string>This app requires camera access to scan classroom attendance QR codes and capture facial enrollment templates.</string>

<!-- Photo Library (Optional fallback) -->
<key>NSPhotoLibraryUsageDescription</key>
<string>This app requires photo library access to upload documented consent references.</string>

<!-- Local HTTP Development Allowance (Disable in Production) -->
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsArbitraryLoads</key>
    <true/>
</dict>
```

#### 2. CocoaPods Install
```bash
cd ios
pod install
cd ..
```

---

## 4. Recommended Project Directory Structure

```text
lib/
├── main.dart
├── core/
│   ├── config/
│   │   └── api_constants.dart      # Base URLs (emulator vs device vs prod)
│   ├── network/
│   │   ├── api_client.dart         # Dio singleton & base options
│   │   └── auth_interceptor.dart   # Injects 'Authorization: Token ...' & handles 401
│   ├── storage/
│   │   └── secure_storage.dart     # flutter_secure_storage wrapper
│   └── theme/
│       └── app_theme.dart          # Colors, typography, buttons
├── features/
│   ├── auth/
│   │   ├── login_screen.dart
│   │   ├── auth_controller.dart
│   │   └── models/user_session.dart
│   ├── student/
│   │   ├── home_dashboard.dart
│   │   ├── qr_scanner_screen.dart  # mobile_scanner implementation
│   │   ├── selfie_enroll_screen.dart # Multi-angle camera capture
│   │   └── attendance_history_screen.dart
│   └── teacher/
│       ├── classes_screen.dart
│       ├── dynamic_qr_screen.dart  # Projector / teacher dynamic QR
│       ├── live_feed_screen.dart   # Real-time attendance ticker
│       └── roster_screen.dart
```

---

## 5. Network Architecture & Base URL Resolution

Android emulators, iOS simulators, and real physical devices resolve local developer machines differently:

### `lib/core/config/api_constants.dart`
```dart
import 'dart:io';

class ApiConstants {
  static String get baseUrl {
    // 1. Android Emulator loopback
    if (Platform.isAndroid) {
      return "http://10.0.2.2:8000/api";
    }
    // 2. iOS Simulator loopback
    if (Platform.isIOS) {
      return "http://127.0.0.1:8000/api";
    }
    // 3. Physical Device over Wi-Fi (Change to your LAN IP)
    return "http://192.168.1.100:8000/api";
  }

  // Auth
  static const String login = "/auth/login/";
  static const String logout = "/auth/logout/";
  static const String profile = "/students/me/";
  static const String scheduleToday = "/students/schedule/today/";

  // Attendance
  static const String checkinQr = "/attendance/checkin/qr/";
  static const String checkinFace = "/attendance/checkin/face/";
  static const String history = "/attendance/history/";
  static const String faceEnroll = "/face/enroll/";

  // Teacher
  static const String teacherClasses = "/teacher/classes/today/";
  static String teacherLiveFeed(int sessionId) => "/teacher/sessions/$sessionId/live-feed/";
  static String teacherDynamicQr(int sessionId) => "/teacher/sessions/$sessionId/qr/dynamic/";
  static String endSession(int sessionId) => "/teacher/sessions/$sessionId/end/";
}
```

---

## 6. Authentication & Secure Token Interceptor

All API calls must carry the authentication header. Implement a Dio interceptor:

### `lib/core/network/auth_interceptor.dart`
```dart
import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class AuthInterceptor extends Interceptor {
  final _storage = const FlutterSecureStorage();

  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    final token = await _storage.read(key: 'auth_token');
    if (token != null && token.isNotEmpty) {
      // Backend FlexibleTokenAuthentication accepts both 'Token <key>' and '<key>'
      options.headers['Authorization'] = 'Token $token';
    }
    options.headers['Accept'] = 'application/json';
    return handler.next(options);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    if (err.response?.statusCode == 401) {
      // Token revoked or expired -> clear storage and redirect to Login
      _storage.deleteAll();
      // Dispatch Navigation event to LoginScreen
    }
    return handler.next(err);
  }
}
```

---

## 7. Key Feature Implementation Snippets

### A. Student Dynamic QR Scanning (`lib/features/student/qr_scanner_screen.dart`)
```dart
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:dio/dio.dart';

class QrScannerScreen extends StatefulWidget {
  final Dio dio;
  const QrScannerScreen({super.key, required this.dio});

  @override
  State<QrScannerScreen> createState() => _QrScannerScreenState();
}

class _QrScannerScreenState extends State<QrScannerScreen> {
  bool _isProcessing = false;

  void _onDetect(BarcodeCapture capture) async {
    if (_isProcessing) return;
    final List<Barcode> barcodes = capture.barcodes;
    if (barcodes.isEmpty) return;

    final String? scannedValue = barcodes.first.rawValue;
    if (scannedValue == null) return;

    setState(() => _isProcessing = true);

    try {
      final response = await widget.dio.post(
        '/attendance/checkin/qr/',
        data: {'qr_token': scannedValue},
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(response.data['message'] ?? 'Check-in successful!'),
            backgroundColor: Colors.green,
          ),
        );
        Navigator.pop(context, true);
      }
    } on DioException catch (e) {
      final errorMsg = e.response?.data['error'] ?? 'Check-in failed.';
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(errorMsg), backgroundColor: Colors.red),
        );
        setState(() => _isProcessing = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Scan Classroom QR Code')),
      body: MobileScanner(onDetect: _onDetect),
    );
  }
}
```

---

### B. Teacher Live Attendance Polling Ticker (`lib/features/teacher/live_feed_screen.dart`)
```dart
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:dio/dio.dart';

class TeacherLiveFeedScreen extends StatefulWidget {
  final int sessionId;
  final Dio dio;
  const TeacherLiveFeedScreen({super.key, required this.sessionId, required this.dio});

  @override
  State<TeacherLiveFeedScreen> createState() => _TeacherLiveFeedScreenState();
}

class _TeacherLiveFeedScreenState extends State<TeacherLiveFeedScreen> {
  Timer? _pollingTimer;
  Map<String, dynamic>? _feedData;

  @override
  void initState() {
    super.initState();
    _fetchLiveFeed();
    // Poll every 3 seconds for real-time check-in updates
    _pollingTimer = Timer.periodic(const Duration(seconds: 3), (_) => _fetchLiveFeed());
  }

  @override
  void dispose() {
    _pollingTimer?.cancel();
    super.dispose();
  }

  Future<void> _fetchLiveFeed() async {
    try {
      final res = await widget.dio.get('/teacher/sessions/${widget.sessionId}/live-feed/');
      if (mounted) {
        setState(() => _feedData = res.data);
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    if (_feedData == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    final recentCheckins = _feedData!['recent_checkins'] as List<dynamic>;

    return Scaffold(
      appBar: AppBar(title: Text('${_feedData!['class_room']} Live Feed')),
      body: Column(
        children: [
          // Stat Counters Card
          Container(
            padding: const EdgeInsets.all(16),
            color: Colors.blue.shade50,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _buildStat('Enrolled', _feedData!['total_enrolled']),
                _buildStat('Present', _feedData!['present_count'], color: Colors.green),
                _buildStat('Late', _feedData!['late_count'], color: Colors.orange),
                _buildStat('Unmarked', _feedData!['unmarked_count'], color: Colors.grey),
              ],
            ),
          ),
          // Live Ticker List
          Expanded(
            child: ListView.builder(
              itemCount: recentCheckins.length,
              itemBuilder: (ctx, i) {
                final item = recentCheckins[i];
                return ListTile(
                  leading: CircleAvatar(
                    backgroundColor: item['status'] == 'present' ? Colors.green : Colors.orange,
                    child: Icon(item['method'] == 'face' ? Icons.face : Icons.qr_code, color: Colors.white),
                  ),
                  title: Text(item['student_name']),
                  subtitle: Text('ID: ${item['student_id']} · Via ${item['method']}'),
                  trailing: Text(item['status'].toString().toUpperCase(),
                    style: const TextStyle(fontWeight: FontWeight.bold)),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStat(String label, dynamic count, {Color color = Colors.black}) {
    return Column(
      children: [
        Text('$count', style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: color)),
        Text(label, style: const TextStyle(fontSize: 12, color: Colors.black54)),
      ],
    );
  }
}
```

---

## 8. Build & Packaging Commands

### A. Android Build

#### 1. Generate Local Debug APK (for Testing)
```bash
flutter build apk --debug
# Output: build/app/outputs/flutter-apk/app-debug.apk
```

#### 2. Generate Release App Bundle (for Google Play Store)
Create `android/key.properties` with your upload keystore, then run:
```bash
flutter build appbundle --release
# Output: build/app/outputs/bundle/release/app-release.aab
```

#### 3. Install Directly to USB Connected Device
```bash
flutter run --release -d <DEVICE_ID>
```

---

### B. iOS Build (Requires macOS)

#### 1. Simulator Debug Run
```bash
open -a Simulator
flutter run -d iPhone
```

#### 2. Production Archive (for TestFlight / App Store)
```bash
flutter build ipa --release
# Output: build/ios/archive/Runner.xcarchive
# Automatically opens Xcode Organizer for distribution
```

---

## 9. QA & Pre-Release Testing Matrix

Test with the pre-seeded backend accounts before pushing to production:

| Target User | Username | Password | Key Feature to Test |
| :--- | :--- | :--- | :--- |
| **Student** | `student_dara` | `StudentPassword123!` | QR Check-In, Face Enrollment Sheet, History |
| **Student** | `student_bopha` | `StudentPassword123!` | Class Schedule, Alert Notifications |
| **Teacher** | `teacher_sokha` | `TeacherPassword123!` | Dynamic Rotating QR Screen, Live Attendance Ticker |
| **Teacher** | `teacher_vanny` | `TeacherPassword123!` | Manual Attendance Override, End Class Session |
