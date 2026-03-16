프로젝트명 : 신설 CNC 기계공정에서의 예지 보전을 위한 데이터 파이프라인 구축

시스템 아키텍처


주요 기술 스택
- Edge & Hardware : ESP32, ADXL345(가속도 센서), FastAPI
- Data Pipline : Apache Kafka, Chimp Compression
- Backend & Monitoring : Spring Boot, InfluxDB, Grafana
- AI & MLOps :  Autoencoder, 1D-ResNet, AWS S3, AWS Lambda, GPU Spot Instance

핵심 해결 과제 및 구현 기능
1. 하이브리드 AI 기반 이상 탐지:
  - Phase 1 (도입 초기): 데이터가 전무한 환경을 극복하기 위해 비지도 학습 모델인 Autoencoder로 정상 패턴 학습 및 이상 징후 탐지
  - Phase 2 (성숙기): 데이터 누적 후 지도 학습 모델인 1D-ResNet으로 전환하여 정밀 진단 수행
2. 데이터 파이프라인 안정성 확보:Kafka의 순차 I/O를 활용해 1Gbps에 달하는 트래픽 홍수 속에서도 유실률 0% 보장
3. Serverless MLOps:AWS Lambda 트리거와 GPU Spot Instance를 연동하여 비용 효율적인 온디맨드(On-demand) 모델 재학습 파이프라인 구축

위험도 분류
- Class 0 : Normal(정상) - 위험도 None
- Class 1 : Axis Imbalance(반경 방향 진동) - 위험도 Normal
- Class 2 : Tilt Imbalance (Z축 진동, 불규칙 파형) - 위험도 High
