# 고장 유형 파악
fault_title = get_fault_label(a_path)

# 데이터 로드
df_n = pd.read_csv(n_path)
df_a = pd.read_csv(a_path)

# 숫자 데이터 컬럼 추출
numeric_cols = df_n.select_dtypes(include=[np.number]).columns.tolist()
if len(numeric_cols) < 5:
    continue

# 분석할 센서 인덱스 설정
sensor_idx = 10 
sensor_name = numeric_cols[sensor_idx]

# --- 그래프 설정 ---
plt.figure(figsize=(14, 9))

# FTA 도표 제목을 맨 위 가운데에 표시
plt.suptitle(
    f"🚨 수직선반 가공 품질 저하 분석 🚨\n"
    f"[고장 유형: {fault_title}]   |   센서: {sensor_name}\n"
    f"Normal: {os.path.basename(n_path)} | Abnormal: {os.path.basename(a_path)}",
    fontsize=18, fontweight='bold', color='darkred', y=0.98
)

# 상단: 정상 데이터 (파랑)
plt.subplot(2, 1, 1)
plt.plot(df_n[sensor_name][:1000], color='#0078D4', linewidth=1.5)
plt.title(f"✅ NORMAL (정상 상태)", fontsize=13, loc='left')
plt.ylabel("Measured Value")
plt.grid(True, alpha=0.3)

# 하단: 고장 데이터 (빨강)
plt.subplot(2, 1, 2)
plt.plot(df_a[sensor_name][:1000], color='#D13438', linewidth=1.5)
plt.title(f"❌ ABNORMAL (이상 발생)", fontsize=13, loc='left')
plt.xlabel("Time Samples")
plt.ylabel("Measured Value")
plt.grid(True, alpha=0.3)

plt.tight_layout(rect=[0, 0.05, 1, 0.95]) # 제목 공간 확보

print(f"▶️ ({i+1}/{total_count}) {fault_title} 데이터 표시 중...")
plt.show()