package com.predictive.monitoring.service;

import com.influxdb.client.InfluxDBClient;
import com.influxdb.client.QueryApi;
import com.influxdb.query.FluxRecord;
import com.influxdb.query.FluxTable;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * InfluxDB 2.x Flux 쿼리 서비스.
 * 센서 시계열 점수 데이터를 조회하여 Grafana 데이터소스에 공급.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class InfluxQueryService {

    private final InfluxDBClient influxDBClient;

    @Value("${influxdb.bucket}")
    private String bucket;

    @Value("${influxdb.org}")
    private String influxOrg;

    /**
     * 특정 기계의 서브시스템별 이상 점수 시계열 조회.
     *
     * @param machineId  기계 ID
     * @param siteId     지점 ID
     * @param rangeMinutes 조회 범위 (분)
     * @return 시계열 데이터 목록
     */
    public List<Map<String, Object>> getTelemetrySeries(
            String siteId,
            String machineId,
            int rangeMinutes
    ) {
        String flux = String.format("""
            from(bucket: "%s")
              |> range(start: -%dm)
              |> filter(fn: (r) => r["_measurement"] == "subsystem_scores")
              |> filter(fn: (r) => r["site_id"] == "%s")
              |> filter(fn: (r) => r["machine_id"] == "%s")
              |> filter(fn: (r) => r["_field"] == "reconstruction_error" or r["_field"] == "is_anomaly")
              |> pivot(rowKey: ["_time", "subsystem"], columnKey: ["_field"], valueColumn: "_value")
              |> sort(columns: ["_time"])
            """, bucket, rangeMinutes, siteId, machineId);

        return executeQuery(flux);
    }

    /**
     * 전체 지점 현재 이상 점수 요약 (가장 최근 30초 윈도우).
     */
    public List<Map<String, Object>> getLatestScores() {
        String flux = String.format("""
            from(bucket: "%s")
              |> range(start: -1m)
              |> filter(fn: (r) => r["_measurement"] == "subsystem_scores")
              |> filter(fn: (r) => r["_field"] == "reconstruction_error")
              |> last()
              |> group(columns: ["site_id", "machine_id", "subsystem"])
            """, bucket);

        return executeQuery(flux);
    }

    private List<Map<String, Object>> executeQuery(String flux) {
        List<Map<String, Object>> results = new ArrayList<>();
        try {
            QueryApi queryApi = influxDBClient.getQueryApi();
            List<FluxTable> tables = queryApi.query(flux, influxOrg);
            for (FluxTable table : tables) {
                for (FluxRecord record : table.getRecords()) {
                    Map<String, Object> row = new HashMap<>(record.getValues());
                    row.put("_time", record.getTime() != null ? record.getTime().toString() : null);
                    results.add(row);
                }
            }
        } catch (Exception e) {
            log.error("InfluxDB 쿼리 실패: {}", e.getMessage());
        }
        return results;
    }
}
