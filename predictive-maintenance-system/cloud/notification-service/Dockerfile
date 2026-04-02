FROM eclipse-temurin:21-jre-jammy
WORKDIR /app

# Gradle wrapper로 빌드된 JAR 복사
COPY build/libs/*.jar app.jar

ENV PORT=8080 \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    DB_URL=jdbc:postgresql://postgres:5432/predictive_maintenance \
    DB_USER=postgres \
    DB_PASS=postgres \
    FIREBASE_CREDENTIALS_FILE=/app/firebase-service-account.json

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/api/alerts/health || exit 1

ENTRYPOINT ["java", "-jar", "app.jar"]
