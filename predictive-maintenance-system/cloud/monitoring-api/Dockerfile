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

ENV PORT=8082 \
    DB_URL=jdbc:postgresql://postgres:5432/predictive_maintenance \
    DB_USER=postgres \
    DB_PASS=postgres \
    INFLUXDB_URL=http://influxdb:8086 \
    INFLUXDB_TOKEN=my-token \
    INFLUXDB_ORG=predictive-maintenance \
    INFLUXDB_BUCKET=sensor-scores

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8082/api/health || exit 1

ENTRYPOINT ["java", "-jar", "app.jar"]
