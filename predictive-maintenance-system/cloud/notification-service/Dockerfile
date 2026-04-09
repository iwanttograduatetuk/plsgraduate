FROM eclipse-temurin:21-jdk-jammy AS builder
WORKDIR /app
COPY gradlew .
COPY gradle gradle
COPY build.gradle .
COPY settings.gradle .
COPY src src
RUN chmod +x gradlew && ./gradlew bootJar -x test --no-daemon

FROM eclipse-temurin:21-jre-jammy
WORKDIR /app
COPY --from=builder /app/build/libs/*.jar app.jar

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
