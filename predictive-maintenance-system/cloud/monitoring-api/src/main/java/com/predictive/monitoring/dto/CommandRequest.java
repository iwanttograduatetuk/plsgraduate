package com.predictive.monitoring.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import lombok.Getter;
import lombok.Setter;

@Getter @Setter
public class CommandRequest {

    /** STOP / RESUME / ACK */
    @NotBlank
    @Pattern(regexp = "STOP|RESUME|ACK")
    private String command;

    /** ACK 시 대상 이벤트 ID (선택) */
    private String eventId;

    private String reason;
}
