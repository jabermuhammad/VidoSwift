# VidoSwift Download Studio — Final Source Bundle

এই ZIP-এ আছে:
- `app/` — Expo React Native Android app; আপনার দেওয়া Download Studio UI-কে মোবাইল UI-তে কাছাকাছি রেখে তৈরি
- `backend/` — FastAPI + yt-dlp API, metadata analysis, quality options এবং server-side download endpoint
- `design-reference/` — আপনার দেওয়া মূল UI archive-এর extracted reference

## আগে জেনে নিন

এটি **সম্পূর্ণ source project bundle**, তৈরি করা signed APK নয়। Android APK বানাতে Android build environment বা EAS Build লাগবে। এই পরিবেশে বাস্তব Android device/emulator-এ ইনস্টল করে সব supported website-এর end-to-end download test করা হয়নি। কোনো third-party website-এর policy, login, anti-bot restriction, regional access, কিংবা yt-dlp পরিবর্তনের কারণে কিছু URL ব্যর্থ হতে পারে। শুধু যে কনটেন্ট ডাউনলোড করার অনুমতি আছে সেটিই ব্যবহার করুন।

## 1) App চালানো (Windows 11)

1. Node.js LTS ইনস্টল থাকতে হবে।
2. `app` ফোল্ডারে CMD খুলুন:

```bat
npm install
npx expo-doctor
npx expo start
```

3. Android ফোনে Expo Go দিয়ে QR scan করে UI/dev build পরীক্ষা করুন। Native production download ও final app behaviour-এর জন্য EAS/Android build ব্যবহার করুন।

API URL বদলাতে `app/src/config.ts`-এর `API_BASE_URL` সম্পাদনা করুন। বর্তমানে সেটি `https://vidoswift.onrender.com`।

**Backend deployment মিলিয়ে নিন:** Render-এর আগের deployment-এর OpenAPI-তে `/v1/download` route ছিল না এবং analyze response-ও এই source-এর app contract থেকে আলাদা ছিল। তাই শুধু app update করলে সেই server থেকে video download হবে না। এই bundle-এর `backend/` নতুন করে Render-এ deploy করুন, তারপর `/openapi.json`-এ `POST /v1/analyze` এবং `GET /v1/download`—দুটিই আছে নিশ্চিত করুন। App এখন দুই ধরনের analyze response বুঝতে পারে এবং missing endpoint হলে পরিষ্কার error দেখায়।

## 2) APK তৈরি (EAS Build)

```bat
npm install -g eas-cli
eas login
eas build:configure
eas build --platform android --profile preview
```

প্রথমবার EAS configuration চাইলে prompt অনুযায়ী এগোন। Build শেষ হলে EAS APK download link দেবে। `app/eas.json`-এ preview APK profile রাখা হয়েছে।

## 3) Backend locally চালানো

Python 3.12 ব্যবহার করে `backend` ফোল্ডারে:

```bat
D:\python-3.12.8-amd64\python.exe -m pip install -r requirements.txt
D:\python-3.12.8-amd64\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Health: `http://127.0.0.1:8000/v1/health`
API docs: `http://127.0.0.1:8000/docs`

Android emulator থেকে host PC-এর backend হলে সাধারণত `http://10.0.2.2:8000` ব্যবহার করতে হয়; physical phone হলে PC-এর LAN IP এবং একই Wi-Fi প্রয়োজন। Release app-এর জন্য HTTPS backend ব্যবহার করুন।

## 4) Existing Render backend update

Backend folder-এর Dockerfile FFmpeg + Python + yt-dlp সহ container বানায়। Repo-তে `backend/` রাখলে deployment root/context অনুযায়ী `backend/Dockerfile` নির্বাচন করুন; root-এ থাকা `render.yaml`-এ Render blueprint-এর configuration দেওয়া আছে। Deploy করার পর `https://YOUR-SERVICE.onrender.com/v1/health` যাচাই করুন এবং app-এর `API_BASE_URL`-এ সেই URL দিন। Render free instance sleep/cold-start হলে প্রথম request ধীর হতে পারে; এটি app-এর UI দিয়ে দূর করা যায় না।

## API contract

- `GET /v1/health`
- `POST /v1/analyze` body: `{"url":"https://..."}`
- `GET /v1/download?url=<encoded-url>&quality=720p`

Backend basic public-host validation, bounded concurrent jobs, useful error responses, temporary-file cleanup এবং FFmpeg-based merge support রাখে। Download endpoint server CPU/RAM/egress ব্যবহার করে; production traffic অনুযায়ী resource limits, authentication/rate limits, storage and abuse prevention যোগ করা উচিত।

## বর্তমান implementation note

- History এবং preferences ডিভাইসে AsyncStorage-এ থাকে।
- Download প্রথমে app-private storage-এ রাখা হয়; Share/Save action দিয়ে অন্য app বা user-selected destination-এ export করা যায়।
- “Wi-Fi only” option সতর্কতামূলক confirmation দেয়; এই build-এ metered-network detection কঠোরভাবে enforce করে না।
- Notification toggle UI preference হিসেবে সংরক্ষিত; background notification service যুক্ত করা হয়নি।
- Clipboard suggestion launch-এ একবার পরীক্ষা হয়; clipboard auto-polling করা হয় না।
