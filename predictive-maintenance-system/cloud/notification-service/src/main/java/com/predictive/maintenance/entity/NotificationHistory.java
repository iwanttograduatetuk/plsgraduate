package com.predictive.maintenance.entity;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.UuidGenerator;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "notification_history")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class NotificationHistory {

    @Id
    @GeneratedValue
    @UuidGenerator
    @Column(name = "notif_id", updatable = false, nullable = false)
    private UUID notifId;

    @Column(name = "event_id", nullable = false)
    private UUID eventId;

    @Column(name = "manager_id")
    private Integer managerId;

    @Column(name = "sent_at", nullable = false)
    private Instant sentAt;

    /** FCM / APNS / WEBSOCKET */
    @Column(name = "channel", nullable = false, length = 20)
    private String channel;

    /** SENT / FAILED */
    @Column(name = "status", nullable = false, length = 10)
    private String status;

    @Column(name = "message", length = 500)
    private String message;
}
