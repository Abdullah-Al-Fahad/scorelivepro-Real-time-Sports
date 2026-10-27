import os
import re
import firebase_admin
from firebase_admin import messaging, credentials
from django.conf import settings
from .models import NotificationLog

def translate_notification(title, body, event_type, lang):
    if lang == 'en':
        return title, body

    translations = {
        'es': {
            'goal_title': "⚽ ¡Gol de {team}!",
            'goal_body': "Marcador actual: {home} {score} {away}",
            'ft_title': "🏁 Final del partido",
            'ft_body': "Resultado final: {home} {score} {away}",
            'ht_title': "⏸️ Descanso",
            'ht_body': "Resultado al descanso: {home} {score} {away}",
            'disallowed_title': "❌ ¡Gol anulado: {team}!",
            'disallowed_body': "El VAR anuló el gol. Marcador actual: {home} {score} {away}",
            'lineup_title': "📋 Alineaciones confirmadas",
            'lineup_body': "El once inicial ya está disponible para el {home} vs {away}",
            'schedule_title': "📅 Calendario de {league}",
            'schedule_body': "Hay {count} partidos mañana en {league}. ¡No te los pierdas!",
            'match_start_title': "⏳ Empieza pronto",
            'match_start_body': "El partido comienza en 15 minutos: {home} vs {away}",
            'card_yellow_title': "🟨 ¡Tarjeta amarilla para {player} ({team})!",
            'card_red_title': "🟥 ¡Tarjeta roja para {player} ({team})!",
            'card_body': "{player} recibió una tarjeta en el minuto {time}'.",
            'sub_title': "🔄 Cambio en {team}",
            'sub_body': "Entra: {player_in} | Sale: {player_out} ({time}')",
            'var_title': "🖥️ Decisión del VAR: {team}",
            'var_body': "{detail} ({time}')",
            'kickoff_title': "⏱️ ¡Inicio del partido: {home} vs {away}!",
            'kickoff_body': "¡El partido ha comenzado oficialmente!",
            'second_half_title': "▶️ Segunda parte en marcha",
            'second_half_body': "Comienza la segunda mitad: {home} {score} {away}",
            'extra_time_title': "⏳ Comienza la prórroga",
            'extra_time_body': "Ha comenzado el tiempo extra: {home} {score} {away}",
            'penalties_title': "🎯 Tanda de penaltis",
            'penalties_body': "¡La tanda de penaltis está en marcha: {home} vs {away}!",
            'missed_penalty_title': "❌ ¡Penalti fallado por {player} ({team})!",
            'missed_penalty_body': "Penalti fallado o detenido en el minuto {time}'.",
            'own_goal_title': "🤦 ¡Gol en propia puerta de {player} ({team})!",
            'own_goal_body': "Marcador actual: {home} {score} {away} ({time}')",
            'postponed_title': "⚠️ Partido aplazado: {home} vs {away}",
            'postponed_body': "El partido ha sido aplazado.",
            'suspended_title': "⚠️ Partido suspendido: {home} vs {away}",
            'suspended_body': "El partido ha sido suspendido temporalmente.",
            'interrupted_title': "⏸️ Partido interrumpido: {home} vs {away}",
            'interrupted_body': "El partido ha sido interrumpido.",
            'abandoned_title': "🛑 Partido abandonado: {home} vs {away}",
            'abandoned_body': "El partido ha sido cancelado definitivamente.",
            'cancelled_title': "🚫 Partido cancelado: {home} vs {away}",
            'cancelled_body': "El partido ha sido cancelado.",
            'walkover_title': "⚖️ Victoria administrativa / Walkover: {home} vs {away}",
            'walkover_body': "El partido terminó por decisión técnica o incomparecencia.",
        },
        'fr': {
            'goal_title': "⚽ But de {team} !",
            'goal_body': "Score actuel : {home} {score} {away}",
            'ft_title': "🏁 Fin du match",
            'ft_body': "Résultat final : {home} {score} {away}",
            'ht_title': "⏸️ Mi-temps",
            'ht_body': "Score à la mi-temps : {home} {score} {away}",
            'disallowed_title': "❌ But refusé : {team}",
            'disallowed_body': "La VAR a annulé le but. Score actuel : {home} {score} {away}",
            'lineup_title': "📋 Compositions disponibles",
            'lineup_body': "Le onze de départ est disponible pour {home} vs {away}",
            'schedule_title': "📅 Calendrier de {league}",
            'schedule_body': "Il y a {count} matchs demain en {league}. Ne les manquez pas !",
            'match_start_title': "⏳ Coup d'envoi imminent",
            'match_start_body': "Le match commence dans 15 min : {home} vs {away}",
            'card_yellow_title': "🟨 Carton jaune pour {player} ({team}) !",
            'card_red_title': "🟥 Carton rouge pour {player} ({team}) !",
            'card_body': "{player} a reçu un carton à la {time}' minute.",
            'sub_title': "🔄 Remplacement pour {team}",
            'sub_body': "Entrant : {player_in} | Sortant : {player_out} ({time}')",
            'var_title': "🖥️ Décision de la VAR : {team}",
            'var_body': "{detail} ({time}')",
            'kickoff_title': "⏱️ Coup d'envoi : {home} vs {away}",
            'kickoff_body': "Le match a officiellement commencé !",
            'second_half_title': "▶️ Début de la seconde période",
            'second_half_body': "La deuxième mi-temps commence : {home} {score} {away}",
            'extra_time_title': "⏳ Début des prolongations",
            'extra_time_body': "Les prolongations ont commencé : {home} {score} {away}",
            'penalties_title': "🎯 Séance de tirs au but",
            'penalties_body': "La séance de tirs au but est lancée : {home} vs {away} !",
            'missed_penalty_title': "❌ Penalty manqué par {player} ({team})",
            'missed_penalty_body': "Penalty manqué ou arrêté à la {time}' minute.",
            'own_goal_title': "🤦 But contre son camp de {player} ({team}) !",
            'own_goal_body': "Score actuel : {home} {score} {away} ({time}')",
            'postponed_title': "⚠️ Match reporté : {home} vs {away}",
            'postponed_body': "Le match a été reporté.",
            'suspended_title': "⚠️ Match suspendu : {home} vs {away}",
            'suspended_body': "Le match a été temporairement suspendu.",
            'interrupted_title': "⏸️ Match interrompu : {home} vs {away}",
            'interrupted_body': "Le match a été interrompu.",
            'abandoned_title': "🛑 Match arrêté : {home} vs {away}",
            'abandoned_body': "Le match a été définitivement arrêté.",
            'cancelled_title': "🚫 Match annulé : {home} vs {away}",
            'cancelled_body': "Le match a été annulé.",
            'walkover_title': "⚖️ Forfait / Victoire sur tapis vert : {home} vs {away}",
            'walkover_body': "Le match a été adjugé sur décision administrative.",
        },
        'de': {
            'goal_title': "⚽ Tor für {team}!",
            'goal_body': "Aktueller Spielstand: {home} {score} {away}",
            'ft_title': "🏁 Spielende",
            'ft_body': "Endergebnis: {home} {score} {away}",
            'ht_title': "⏸️ Halbzeit",
            'ht_body': "Halbzeitstand: {home} {score} {away}",
            'disallowed_title': "❌ Tor aberkannt: {team}",
            'disallowed_body': "VAR hat das Tor aberkannt. Aktueller Spielstand: {home} {score} {away}",
            'lineup_title': "📋 Aufstellungen bestätigt",
            'lineup_body': "Die Startaufstellung für {home} gegen {away} ist jetzt verfügbar",
            'schedule_title': "📅 Spielplan für {league}",
            'schedule_body': "Morgen stehen {count} Spiele in {league} an. Verpasse es nicht!",
            'match_start_title': "⏳ Anpfiff in Kürze",
            'match_start_body': "Das Spiel beginnt in 15 Minuten: {home} gegen {away}",
            'card_yellow_title': "🟨 Gelbe Karte für {player} ({team})!",
            'card_red_title': "🟥 Rote Karte für {player} ({team})!",
            'card_body': "{player} erhielt eine karte in der {time}'. Minute.",
            'sub_title': "🔄 Auswechslung bei {team}",
            'sub_body': "Ein: {player_in} | Aus: {player_out} ({time}')",
            'var_title': "🖥️ VAR-Entscheidung: {team}",
            'var_body': "{detail} ({time}')",
            'kickoff_title': "⏱️ Anpfiff: {home} gegen {away}",
            'kickoff_body': "Das Spiel hat offiziell begonnen!",
            'second_half_title': "▶️ Beginn der 2. Halbzeit",
            'second_half_body': "Die 2. Halbzeit hat begonnen: {home} {score} {away}",
            'extra_time_title': "⏳ Verlängerung begonnen",
            'extra_time_body': "Die Verlängerung läuft: {home} {score} {away}",
            'penalties_title': "🎯 Elfmeterschießen",
            'penalties_body': "Das Elfmeterschießen läuft: {home} gegen {away}!",
            'missed_penalty_title': "❌ Elfmeter verschossen von {player} ({team})",
            'missed_penalty_body': "Elfmeter vergeben in der {time}'. Minute.",
            'own_goal_title': "🤦 Eigentor von {player} ({team})!",
            'own_goal_body': "Aktueller Spielstand: {home} {score} {away} ({time}')",
            'postponed_title': "⚠️ Spiel verschoben: {home} gegen {away}",
            'postponed_body': "Das Spiel wurde verschoben.",
            'suspended_title': "⚠️ Spiel unterbrochen: {home} gegen {away}",
            'suspended_body': "Das Spiel wurde vorübergehend unterbrochen.",
            'interrupted_title': "⏸️ Spiel unterbrochen: {home} gegen {away}",
            'interrupted_body': "Das Spiel wurde unterbrochen.",
            'abandoned_title': "🛑 Spiel abgebrochen: {home} gegen {away}",
            'abandoned_body': "Das Spiel wurde vorzeitig abgebrochen.",
            'cancelled_title': "🚫 Spiel abgesagt: {home} gegen {away}",
            'cancelled_body': "Das Spiel wurde abgesagt.",
            'walkover_title': "⚖️ Wertung / Forfait: {home} gegen {away}",
            'walkover_body': "Das Spiel wurde am grünen Tisch gewertet.",
        },
        'it': {
            'goal_title': "⚽ Gol di {team}!",
            'goal_body': "Risultato attuale: {home} {score} {away}",
            'ft_title': "🏁 Fischio finale",
            'ft_body': "Risultato finale: {home} {score} {away}",
            'ht_title': "⏸️ Fine primo tempo",
            'ht_body': "Risultato all'intervallo: {home} {score} {away}",
            'disallowed_title': "❌ Gol annullato: {team}",
            'disallowed_body': "Il VAR ha annullato il gol. Risultato attuale: {home} {score} {away}",
            'lineup_title': "📋 Formazioni ufficiali",
            'lineup_body': "L'undici titolare è ora disponibile per {home} vs {away}",
            'schedule_title': "📅 Calendario {league}",
            'schedule_body': "Ci sono {count} partite domani in {league}. Non perdere l'appuntamento!",
            'match_start_title': "⏳ Calcio d'inizio imminente",
            'match_start_body': "La partita inizia tra 15 minuti: {home} vs {away}",
            'card_yellow_title': "🟨 Cartellino giallo per {player} ({team})!",
            'card_red_title': "🟥 Cartellino rosso per {player} ({team})!",
            'card_body': "{player} ha ricevuto un cartellino al minuto {time}'.",
            'sub_title': "🔄 Sostituzione per il {team}",
            'sub_body': "Entra: {player_in} | Esce: {player_out} ({time}')",
            'var_title': "🖥️ Decisione VAR: {team}",
            'var_body': "{detail} ({time}')",
            'kickoff_title': "⏱️ Calcio d'inizio: {home} vs {away}",
            'kickoff_body': "La partita è ufficialmente iniziata!",
            'second_half_title': "▶️ Inizio secondo tempo",
            'second_half_body': "È iniziato il secondo tempo: {home} {score} {away}",
            'extra_time_title': "⏳ Inizio tempi supplementari",
            'extra_time_body': "Sono iniziati i supplementari: {home} {score} {away}",
            'penalties_title': "🎯 Calci di rigore",
            'penalties_body': "Calci di rigore in corso: {home} vs {away}!",
            'missed_penalty_title': "❌ Rigore sbagliato da {player} ({team})",
            'missed_penalty_body': "Rigore fallito o parato al minuto {time}'.",
            'own_goal_title': "🤦 Autogol di {player} ({team})!",
            'own_goal_body': "Risultato attuale: {home} {score} {away} ({time}')",
            'postponed_title': "⚠️ Partita rinviata: {home} vs {away}",
            'postponed_body': "La partita è stata rinviata.",
            'suspended_title': "⚠️ Partita sospesa: {home} vs {away}",
            'suspended_body': "La partita è stata temporaneamente sospesa.",
            'interrupted_title': "⏸️ Partita interrotta: {home} vs {away}",
            'interrupted_body': "La partita è stata interrotta.",
            'abandoned_title': "🛑 Partita abbandonata: {home} vs {away}",
            'abandoned_body': "La partita è stata definitivamente sospesa.",
            'cancelled_title': "🚫 Partita annullata: {home} vs {away}",
            'cancelled_body': "La partita è stata annullata.",
            'walkover_title': "⚖️ Vittoria a tavolino: {home} vs {away}",
            'walkover_body': "Partita decisa a tavolino o per rinuncia.",
        },
        'pt': {
            'goal_title': "⚽ Golo de {team}!",
            'goal_body': "Resultado atual: {home} {score} {away}",
            'ft_title': "🏁 Fim do jogo",
            'ft_body': "Resultado final: {home} {score} {away}",
            'ht_title': "⏸️ Intervalo",
            'ht_body': "Resultado ao intervalo: {home} {score} {away}",
            'disallowed_title': "❌ Golo anulado: {team}",
            'disallowed_body': "O VAR anulou o golo. Resultado atual: {home} {score} {away}",
            'lineup_title': "📋 Escalações confirmadas",
            'lineup_body': "A escalação inicial já está disponível para {home} vs {away}",
            'schedule_title': "📅 Jogos de {league}",
            'schedule_body': "Há {count} jogos amanhã em {league}. Não perca!",
            'match_start_title': "⏳ Início em breve",
            'match_start_body': "O jogo começa em 15 minutos: {home} vs {away}",
            'card_yellow_title': "🟨 Cartão amarelo para {player} ({team})!",
            'card_red_title': "🟥 Cartão vermelho para {player} ({team})!",
            'card_body': "{player} recebeu um cartão no minuto {time}'.",
            'sub_title': "🔄 Substituição no {team}",
            'sub_body': "Entra: {player_in} | Sai: {player_out} ({time}')",
            'var_title': "🖥️ Decisão do VAR: {team}",
            'var_body': "{detail} ({time}')",
            'kickoff_title': "⏱️ Pontapé de saída: {home} vs {away}",
            'kickoff_body': "O jogo começou oficialmente!",
            'second_half_title': "▶️ Início da 2ª parte",
            'second_half_body': "Começou o segundo tempo: {home} {score} {away}",
            'extra_time_title': "⏳ Início do prolongamento",
            'extra_time_body': "Começou o tempo extra: {home} {score} {away}",
            'penalties_title': "🎯 Disputa de penáltis",
            'penalties_body': "A disputa de grandes penalidades começou: {home} vs {away}!",
            'missed_penalty_title': "❌ Penálti falhado por {player} ({team})",
            'missed_penalty_body': "Penálti falhado ou defendido aos {time}' minutos.",
            'own_goal_title': "🤦 Golo na própria baliza de {player} ({team})!",
            'own_goal_body': "Resultado atual: {home} {score} {away} ({time}')",
            'postponed_title': "⚠️ Jogo adiado: {home} vs {away}",
            'postponed_body': "A partida foi adiada.",
            'suspended_title': "⚠️ Jogo suspenso: {home} vs {away}",
            'suspended_body': "A partida foi suspensa temporariamente.",
            'interrupted_title': "⏸️ Jogo interrompido: {home} vs {away}",
            'interrupted_body': "A partida foi interrompida.",
            'abandoned_title': "🛑 Jogo abandonado: {home} vs {away}",
            'abandoned_body': "A partida foi terminada prematuramente.",
            'cancelled_title': "🚫 Jogo cancelado: {home} vs {away}",
            'cancelled_body': "A partida foi cancelada.",
            'walkover_title': "⚖️ Vitória por falta de comparência: {home} vs {away}",
            'walkover_body': "O jogo foi decidido por decisão administrativa.",
        },
        'tr': {
            'goal_title': "⚽ {team} Gol Attı!",
            'goal_body': "Mevcut Skor: {home} {score} {away}",
            'ft_title': "🏁 Maç Sonucu",
            'ft_body': "Maç Sonucu: {home} {score} {away}",
            'ht_title': "⏸️ İlk Yarı Sonucu",
            'ht_body': "İlk Yarı Skoru: {home} {score} {away}",
            'disallowed_title': "❌ Gol İptal Edildi: {team}",
            'disallowed_body': "VAR incelemesi sonrası gol iptal edildi. Skor: {home} {score} {away}",
            'lineup_title': "📋 İlk 11'ler Belli Oldu",
            'lineup_body': "{home} - {away} karşılaşmasının ilk 11'leri açıklandı",
            'schedule_title': "📅 {league} Fikstürü",
            'schedule_body': "Yarın {league} liginde {count} maç oynanacak. Kaçırmayın!",
            'match_start_title': "⏳ Başlamasına Az Kaldı",
            'match_start_body': "Karşılaşma 15 dakika içinde başlayacak: {home} - {away}",
            'card_yellow_title': "🟨 {player} Sarı Kart Gördü ({team})!",
            'card_red_title': "🟥 {player} Kırmızı Kart Gördü ({team})!",
            'card_body': "{player} {time}'. dakikada kart gördü.",
            'sub_title': "🔄 {team} Takımında Oyuncu Değişikliği",
            'sub_body': "Giren: {player_in} | Çıkan: {player_out} ({time}')",
            'var_title': "🖥️ VAR Kararı: {team}",
            'var_body': "{detail} ({time}')",
            'kickoff_title': "⏱️ Maç Başladı: {home} - {away}",
            'kickoff_body': "Karşılaşma resmen başladı!",
            'second_half_title': "▶️ İkinci Yarı Başladı",
            'second_half_body': "İkinci yarı başladı: {home} {score} {away}",
            'extra_time_title': "⏳ Uzatmalar Başladı",
            'extra_time_body': "Uzatma devreleri başladı: {home} {score} {away}",
            'penalties_title': "🎯 Seri Penaltı Atışları",
            'penalties_body': "Penaltı atışları başladı: {home} - {away}!",
            'missed_penalty_title': "❌ {player} Penaltıyı Kaçırdı ({team})",
            'missed_penalty_body': "{time}'. dakikada penaltı kaçırıldı.",
            'own_goal_title': "🤦 {player} Kendi Kalesine Gol Attı ({team})!",
            'own_goal_body': "Mevcut Skor: {home} {score} {away} ({time}')",
            'postponed_title': "⚠️ Maç Ertelendi: {home} - {away}",
            'postponed_body': "Karşılaşma ertelendi.",
            'suspended_title': "⚠️ Maç Askıya Alındı: {home} - {away}",
            'suspended_body': "Karşılaşma geçici olarak durduruldu.",
            'interrupted_title': "⏸️ Maç Yarıda Kesildi: {home} - {away}",
            'interrupted_body': "Karşılaşmaya ara verildi.",
            'abandoned_title': "🛑 Maç Tatil Edildi: {home} - {away}",
            'abandoned_body': "Karşılaşma tatil edildi.",
            'cancelled_title': "🚫 Maç İptal Edildi: {home} - {away}",
            'cancelled_body': "Karşılaşma iptal edildi.",
            'walkover_title': "⚖️ Hükmen Galibiyet: {home} - {away}",
            'walkover_body': "Karşılaşma hükmen sonuçlandı.",
        }
    }

    t_map = translations.get(lang)
    if not t_map:
        return title, body

    if event_type == 'GOAL':
        m_title = re.search(r"(?:Goal by|Gol di|But de|Tor für)\s+(.+?)(?:\!|$)", title, re.IGNORECASE)
        team = m_title.group(1).strip() if m_title else ""
        m_body = re.search(r"(?:Score|Result|Risultato|Spielstand|Marcador).*?:\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
        else:
            clean_body = re.sub(r"^.*?:", "", body).strip()
            score_match = re.search(r"(\d+\s*-\s*\d+)", clean_body)
            if score_match:
                score = score_match.group(1).strip()
                parts = clean_body.split(score)
                home = parts[0].strip()
                away = parts[1].strip() if len(parts) > 1 else ""
            else:
                home, score, away = "", "", ""
        new_title = t_map['goal_title'].format(team=team)
        new_body = t_map['goal_body'].format(home=home, score=score, away=away)
        return new_title, new_body

    elif event_type == 'FULL_TIME':
        m_body = re.search(r"(?:Result|Risultato|Spielstand|Marcador).*?:\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
        else:
            clean_body = re.sub(r"^.*?:", "", body).strip()
            score_match = re.search(r"(\d+\s*-\s*\d+)", clean_body)
            if score_match:
                score = score_match.group(1).strip()
                parts = clean_body.split(score)
                home = parts[0].strip()
                away = parts[1].strip() if len(parts) > 1 else ""
            else:
                home, score, away = "", "", ""
        new_title = t_map['ft_title']
        new_body = t_map['ft_body'].format(home=home, score=score, away=away)
        return new_title, new_body

    elif event_type == 'LINEUPS':
        m_body = re.search(r"(?:available for|disponible pour|bestätigt für|disponibile per|açıklandı)\s*(.+?)\s+(?:vs|-)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, away = m_body.group(1).strip(), m_body.group(2).strip()
        else:
            clean_body = re.sub(r"^.*?for\s+", "", body, flags=re.IGNORECASE).strip()
            parts = re.split(r"\s+(?:vs|-)\s+", clean_body, flags=re.IGNORECASE)
            home = parts[0].strip() if len(parts) > 0 else ""
            away = parts[1].strip() if len(parts) > 1 else ""
        new_title = t_map['lineup_title']
        new_body = t_map['lineup_body'].format(home=home, away=away)
        return new_title, new_body

    elif event_type == 'SCHEDULE':
        league = re.sub(r"\s+Schedule.*$", "", title, flags=re.IGNORECASE).strip()
        league = re.sub(r"^[📅\s]*", "", league).strip()
        m_body = re.search(r"(?:There are|Il y a|Morgen stehen|Ci son|Há|Yarın)\s*(\d+)\s*(?:matches|matchs|Spiele|partite|jogos|maç)", body, re.IGNORECASE)
        if m_body:
            count = m_body.group(1)
        else:
            count = "0"
        new_title = t_map['schedule_title'].format(league=league)
        new_body = t_map['schedule_body'].format(count=count, league=league)
        return new_title, new_body

    elif event_type == 'MATCH_START':
        m_body = re.search(r"(?:15 mins:|15 min:|15 minutos:|15 dakika içinde:|imminente)\s*(.+?)\s+(?:vs|-)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, away = m_body.group(1).strip(), m_body.group(2).strip()
        else:
            clean_body = re.sub(r"^.*?(?:15 mins:|15 min:|15 minutos:|15 dakika içinde:)\s*", "", body, flags=re.IGNORECASE).strip()
            parts = re.split(r"\s+(?:vs|-)\s+", clean_body, flags=re.IGNORECASE)
            home = parts[0].strip() if len(parts) > 0 else ""
            away = parts[1].strip() if len(parts) > 1 else ""
        new_title = t_map['match_start_title']
        new_body = t_map['match_start_body'].format(home=home, away=away)
        return new_title, new_body

    elif event_type == 'CARD':
        is_red = '🟥' in title or 'Red' in title or 'second yellow' in title.lower()
        m_title = re.search(r"Card for\s+(.+?)\s+\((.+?)\)", title, re.IGNORECASE)
        player = m_title.group(1).strip() if m_title else ""
        team = m_title.group(2).strip() if m_title else ""
        
        m_body = re.search(r"in the\s+(\d+)|(\d+)'\s+minute|minute\s+(\d+)", body, re.IGNORECASE)
        time = ""
        if m_body:
            time = m_body.group(1) or m_body.group(2) or m_body.group(3)
        if not time:
            time_match = re.search(r"(\d+)", body)
            time = time_match.group(1) if time_match else ""

        key = 'card_red_title' if is_red else 'card_yellow_title'
        new_title = t_map[key].format(player=player, team=team)
        new_body = t_map['card_body'].format(player=player, time=time)
        return new_title, new_body

    elif event_type == 'SUBSTITUTION':
        m_title = re.search(r"for\s+(.+)$", title, re.IGNORECASE)
        team = m_title.group(1).strip() if m_title else ""
        
        m_body = re.search(r"In:\s*(.+?)\s*\|\s*Out:\s*(.+?)\s*\((.+?)\)", body, re.IGNORECASE)
        if m_body:
            player_in = m_body.group(1).strip()
            player_out = m_body.group(2).strip()
            time = m_body.group(3).replace("'", "").strip()
        else:
            player_in, player_out, time = "", "", ""
            
        new_title = t_map['sub_title'].format(team=team)
        new_body = t_map['sub_body'].format(player_in=player_in, player_out=player_out, time=time)
        return new_title, new_body

    elif event_type == 'VAR':
        m_title = re.search(r"Decision:\s*(.+)$", title, re.IGNORECASE)
        team = m_title.group(1).strip() if m_title else ""
        
        m_body = re.search(r"(.+?)\s*\((.+?)\)", body, re.IGNORECASE)
        if m_body:
            detail = m_body.group(1).strip()
            time = m_body.group(2).replace("'", "").strip()
        else:
            detail = body
            time = ""
            
        new_title = t_map['var_title'].format(team=team)
        new_body = t_map['var_body'].format(detail=detail, time=time)
        return new_title, new_body

    elif event_type == 'HALF_TIME':
        m_body = re.search(r"(?:Score|Result|Risultato|Spielstand|Marcador|Skoru|intervallo).*?:\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
        else:
            clean_body = re.sub(r"^.*?:", "", body).strip()
            score_match = re.search(r"(\d+\s*-\s*\d+)", clean_body)
            if score_match:
                score = score_match.group(1).strip()
                parts = clean_body.split(score)
                home = parts[0].strip()
                away = parts[1].strip() if len(parts) > 1 else ""
            else:
                home, score, away = "", "", ""
        new_title = t_map['ht_title']
        new_body = t_map['ht_body'].format(home=home, score=score, away=away)
        return new_title, new_body

    elif event_type == 'DISALLOWED_GOAL':
        m_title = re.search(r"(?:Disallowed|anulado|refusé|aberkannt|annullato|İptal Edildi):\s*(.+?)(?:\!|$)", title, re.IGNORECASE)
        team = m_title.group(1).strip() if m_title else ""
        m_body = re.search(r"(?:Score|Result|Risultato|Spielstand|Marcador|Skor).*?:\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
        else:
            clean_body = re.sub(r"^.*?:", "", body).strip()
            score_match = re.search(r"(\d+\s*-\s*\d+)", clean_body)
            if score_match:
                score = score_match.group(1).strip()
                parts = clean_body.split(score)
                home = parts[0].strip()
                away = parts[1].strip() if len(parts) > 1 else ""
            else:
                home, score, away = "", "", ""
        new_title = t_map['disallowed_title'].format(team=team)
        new_body = t_map['disallowed_body'].format(home=home, score=score, away=away)
        return new_title, new_body

    elif event_type == 'KICKOFF':
        m_title = re.search(r":\s*(.+?)\s+(?:vs|-)\s+(.+)", title, re.IGNORECASE)
        home = m_title.group(1).strip() if m_title else ""
        away = m_title.group(2).strip() if m_title else ""
        new_title = t_map['kickoff_title'].format(home=home, away=away)
        new_body = t_map['kickoff_body']
        return new_title, new_body

    elif event_type == 'SECOND_HALF':
        m_body = re.search(r"(?:begun|commence|hat begonnen|iniziato|Começou|başladı):\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
        else:
            home, score, away = "", "", ""
        new_title = t_map['second_half_title']
        new_body = t_map['second_half_body'].format(home=home, score=score, away=away)
        return new_title, new_body

    elif event_type == 'EXTRA_TIME':
        m_body = re.search(r"(?:begun|commencé|läuft|iniziati|Começou|başladı):\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
        else:
            home, score, away = "", "", ""
        new_title = t_map['extra_time_title']
        new_body = t_map['extra_time_body'].format(home=home, score=score, away=away)
        return new_title, new_body

    elif event_type == 'PENALTY_SHOOTOUT':
        m_body = re.search(r":\s*(.+?)\s+(?:vs|-)\s+(.+?)(?:\!|$)", body, re.IGNORECASE)
        home = m_body.group(1).strip() if m_body else ""
        away = m_body.group(2).strip() if m_body else ""
        new_title = t_map['penalties_title']
        new_body = t_map['penalties_body'].format(home=home, away=away)
        return new_title, new_body

    elif event_type == 'MISSED_PENALTY':
        m_title = re.search(r"by\s+(.+?)\s+\((.+?)\)", title, re.IGNORECASE)
        player = m_title.group(1).strip() if m_title else ""
        team = m_title.group(2).strip() if m_title else ""
        m_time = re.search(r"(\d+)", body)
        time = m_time.group(1) if m_time else ""
        new_title = t_map['missed_penalty_title'].format(player=player, team=team)
        new_body = t_map['missed_penalty_body'].format(time=time)
        return new_title, new_body

    elif event_type == 'OWN_GOAL':
        m_title = re.search(r"by\s+(.+?)\s+\((.+?)\)", title, re.IGNORECASE)
        player = m_title.group(1).strip() if m_title else ""
        team = m_title.group(2).strip() if m_title else ""
        m_body = re.search(r":\s*(.+?)\s+(\d+\s*-\s*\d+)\s+(.+?)\s*(?:\((\d+)'\)|$)", body, re.IGNORECASE)
        if m_body:
            home, score, away = m_body.group(1).strip(), m_body.group(2).strip(), m_body.group(3).strip()
            time = m_body.group(4).strip() if m_body.group(4) else ""
        else:
            home, score, away, time = "", "", "", ""
        new_title = t_map['own_goal_title'].format(player=player, team=team)
        new_body = t_map['own_goal_body'].format(home=home, score=score, away=away, time=time)
        return new_title, new_body

    elif event_type in ['POSTPONED', 'SUSPENDED', 'INTERRUPTED', 'ABANDONED', 'CANCELLED', 'WALKOVER']:
        m_title = re.search(r":\s*(.+?)\s+(?:vs|-)\s+(.+)", title, re.IGNORECASE)
        home = m_title.group(1).strip() if m_title else ""
        away = m_title.group(2).strip() if m_title else ""
        key_map = {
            'POSTPONED': ('postponed_title', 'postponed_body'),
            'SUSPENDED': ('suspended_title', 'suspended_body'),
            'INTERRUPTED': ('interrupted_title', 'interrupted_body'),
            'ABANDONED': ('abandoned_title', 'abandoned_body'),
            'CANCELLED': ('cancelled_title', 'cancelled_body'),
            'WALKOVER': ('walkover_title', 'walkover_body'),
        }
        t_key, b_key = key_map[event_type]
        new_title = t_map[t_key].format(home=home, away=away)
        new_body = t_map[b_key]
        return new_title, new_body

    return title, body

def update_device_topic_subscriptions(device, old_lang, new_lang):
    if not device or not device.registration_id or not old_lang or not new_lang or old_lang == new_lang:
        return

    user = device.user
    notifications_enabled = bool(device.active)
    if user and hasattr(user, 'fan_profile') and not user.fan_profile.receive_live_notifications:
        notifications_enabled = False

    if not notifications_enabled:
        unsubscribe_deactivated_device(device)
        return
    
    NotificationService.ensure_firebase_initialized()
    
    team_ids = []
    league_ids = []
    fixture_ids = []
    
    if device.user:
        if hasattr(device.user, 'fan_profile'):
            profile = device.user.fan_profile
            team_ids = list(profile.favorite_teams.values_list('id', flat=True))
            league_ids = list(profile.favorite_leagues.values_list('id', flat=True))
            fixture_ids = list(profile.favorite_fixtures.values_list('id', flat=True))
    elif device.guest_id:
        from users.models import GuestFavorite
        fav = GuestFavorite.objects.filter(device_id=device.guest_id).first()
        if fav:
            team_ids = list(fav.favorite_teams.values_list('id', flat=True))
            league_ids = list(fav.favorite_leagues.values_list('id', flat=True))
            fixture_ids = list(fav.favorite_fixtures.values_list('id', flat=True))
            
    languages = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
    # Unsubscribe from ALL other languages to prevent multi-language subscription leaks
    other_langs = [l for l in languages if l != new_lang]
    if old_lang and old_lang not in other_langs and old_lang != new_lang:
        other_langs.append(old_lang)

    for l in other_langs:
        for tid in team_ids:
            try:
                messaging.unsubscribe_from_topic([device.registration_id], f"team_{tid}_{l}")
            except Exception: pass
        for lid in league_ids:
            try:
                messaging.unsubscribe_from_topic([device.registration_id], f"league_{lid}_{l}")
            except Exception: pass
        for fid in fixture_ids:
            try:
                messaging.unsubscribe_from_topic([device.registration_id], f"match_{fid}_{l}")
                messaging.unsubscribe_from_topic([device.registration_id], f"fixture_{fid}_{l}")
            except Exception: pass
        try:
            messaging.unsubscribe_from_topic([device.registration_id], f"global_{l}")
        except Exception: pass

    # Also unsubscribe from legacy unadorned base topics
    for tid in team_ids:
        try: messaging.unsubscribe_from_topic([device.registration_id], f"team_{tid}")
        except Exception: pass
    for lid in league_ids:
        try: messaging.unsubscribe_from_topic([device.registration_id], f"league_{lid}")
        except Exception: pass
    for fid in fixture_ids:
        try:
            messaging.unsubscribe_from_topic([device.registration_id], f"match_{fid}")
            messaging.unsubscribe_from_topic([device.registration_id], f"fixture_{fid}")
        except Exception: pass
    try: messaging.unsubscribe_from_topic([device.registration_id], "global")
    except Exception: pass

    # Subscribe to new language topics
    new_topics = (
        [f"team_{tid}_{new_lang}" for tid in team_ids] + 
        [f"league_{lid}_{new_lang}" for lid in league_ids] +
        [f"match_{fid}_{new_lang}" for fid in fixture_ids] +
        [f"fixture_{fid}_{new_lang}" for fid in fixture_ids] +
        [f"global_{new_lang}"]
    )
    for topic in new_topics:
        try:
            messaging.subscribe_to_topic([device.registration_id], topic)
            print(f"Language migration: subscribed {device.registration_id} to {topic}")
        except Exception as e:
            print(f"Error subscribing {device.registration_id} to {topic}: {e}")

def sync_device_subscriptions(device):
    """
    Subscribes a device registration token to all fanned-out topics of its owner's favorites.
    Called when a device token is registered or updated.
    """
    if not device or not device.registration_id:
        return

    user = device.user
    if not device.active or (user and hasattr(user, 'fan_profile') and not user.fan_profile.receive_live_notifications):
        unsubscribe_deactivated_device(device)
        return

    NotificationService.ensure_firebase_initialized()
    guest_id = device.guest_id
    
    # Priority: device language if set -> user profile language if non-English -> default 'en'
    lang = device.language
    if (not lang or lang == 'en') and user and hasattr(user, 'fan_profile') and user.fan_profile.language:
        lang = user.fan_profile.language
    if not lang:
        lang = 'en'

    if device.language != lang:
        device.language = lang
        device.save(update_fields=['language'])
    
    team_ids = []
    league_ids = []
    fixture_ids = []
    
    if user:
        if hasattr(user, 'fan_profile'):
            profile = user.fan_profile
            team_ids = list(profile.favorite_teams.values_list('id', flat=True))
            league_ids = list(profile.favorite_leagues.values_list('id', flat=True))
            fixture_ids = list(profile.favorite_fixtures.values_list('id', flat=True))
    elif guest_id:
        from users.models import GuestFavorite
        fav = GuestFavorite.objects.filter(device_id=guest_id).first()
        if fav:
            team_ids = list(fav.favorite_teams.values_list('id', flat=True))
            league_ids = list(fav.favorite_leagues.values_list('id', flat=True))
            fixture_ids = list(fav.favorite_fixtures.values_list('id', flat=True))
            
    languages = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
    other_langs = [l for l in languages if l != lang]

    # Subscribe to team topics in device's preferred language & clean up legacy and other language topics
    for tid in team_ids:
        try:
            NotificationService.subscribe_tokens_to_topic([device.registration_id], f"team_{tid}_{lang}")
            NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"team_{tid}")
            for ol in other_langs:
                NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"team_{tid}_{ol}")
        except Exception as e:
            print(f"Failed to subscribe device {device.id} to team_{tid}_{lang}: {e}")
            
    # Subscribe to league topics in device's preferred language & clean up legacy and other language topics
    for lid in league_ids:
        try:
            NotificationService.subscribe_tokens_to_topic([device.registration_id], f"league_{lid}_{lang}")
            NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"league_{lid}")
            for ol in other_langs:
                NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"league_{lid}_{ol}")
        except Exception as e:
            print(f"Failed to subscribe device {device.id} to league_{lid}_{lang}: {e}")
            
    # Subscribe to fixture/match topics in device's preferred language & clean up legacy and other language topics
    for fid in fixture_ids:
        try:
            NotificationService.subscribe_tokens_to_topic([device.registration_id], f"match_{fid}_{lang}")
            NotificationService.subscribe_tokens_to_topic([device.registration_id], f"fixture_{fid}_{lang}")
            NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"match_{fid}")
            NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"fixture_{fid}")
            for ol in other_langs:
                NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"match_{fid}_{ol}")
                NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"fixture_{fid}_{ol}")
        except Exception as e:
            print(f"Failed to subscribe device {device.id} to match/fixture {fid}_{lang}: {e}")

    # Subscribe to global topic in device's preferred language & clean up legacy and other language topics
    try:
        NotificationService.subscribe_tokens_to_topic([device.registration_id], f"global_{lang}")
        NotificationService.unsubscribe_tokens_from_topic([device.registration_id], "global")
        for ol in other_langs:
            NotificationService.unsubscribe_tokens_from_topic([device.registration_id], f"global_{ol}")
    except Exception as e:
        print(f"Failed to subscribe device {device.id} to global_{lang}: {e}")


class NotificationService:
    update_device_topic_subscriptions = staticmethod(update_device_topic_subscriptions)
    sync_device_subscriptions = staticmethod(sync_device_subscriptions)
    @staticmethod
    def ensure_firebase_initialized():
        """
        Lazily initialize Firebase Admin SDK.
        Ensures Celery workers never fail silently if apps.py couldn't initialize.
        """
        if not firebase_admin._apps:
            if getattr(settings, 'FIREBASE_CONFIGURED', False):
                try:
                    cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
                    firebase_admin.initialize_app(cred)
                    print("Firebase Admin lazily initialized in NotificationService.")
                except Exception as e:
                    print(f"CRITICAL: Failed to initialize Firebase in NotificationService: {e}")
                    # Log if the file is a directory (Docker volume common issue) or missing
                    if not os.path.exists(settings.FIREBASE_CREDENTIALS_PATH):
                        print("ERROR: firebase-credentials.json is COMPLETELY MISSING!")
                    elif os.path.isdir(settings.FIREBASE_CREDENTIALS_PATH):
                        print("ERROR: firebase-credentials.json is a DIRECTORY, not a file!")
                    else:
                        try:
                            with open(settings.FIREBASE_CREDENTIALS_PATH, 'r') as f:
                                content = f.read()
                                print(f"\n{'='*60}")
                                print(f"📄 FIREBASE JSON CONTENTS MOUNTED IN DOCKER:")
                                print(content)
                                print(f"{'='*60}\n")
                        except Exception as read_err:
                            print(f"Could not read the file for debugging: {read_err}")
            else:
                print("Firebase Admin SDK is in Simulator Mode (missing or placeholder credentials). Skipping initialization.")

    @staticmethod
    def send_push_to_topic(topic, title, body, data=None, event_type='CUSTOM', is_internal=False):
        """
        Sends a message to a topic and logs it with the specific event type.
        Supports language-specific topic fan-out and user-specific translations.
        """
        # Ensure data is dict and make a copy to avoid mutating the caller's dictionary
        data = data.copy() if data is not None else {}
        
        # Enforce click_action
        if "click_action" not in data:
            data["click_action"] = "FLUTTER_NOTIFICATION_CLICK"
            
        # Map event_type to routing type if type is missing or incorrect
        if "type" not in data:
            if event_type in [
                'GOAL', 'FULL_TIME', 'LINEUPS', 'MATCH_START', 'CARD', 'SUBSTITUTION',
                'VAR', 'HALF_TIME', 'DISALLOWED_GOAL', 'KICKOFF', 'SECOND_HALF',
                'EXTRA_TIME', 'PENALTY_SHOOTOUT', 'MISSED_PENALTY', 'OWN_GOAL',
                'POSTPONED', 'SUSPENDED', 'INTERRUPTED', 'ABANDONED', 'CANCELLED',
                'WALKOVER'
            ]:
                data["type"] = "match"
            elif event_type in ['SCHEDULE', 'LEAGUE_UPDATE', 'TABLE_UPDATE', 'TOP_SCORER']:
                data["type"] = "league"
            elif event_type in ['TEAM_NEWS']:
                data["type"] = "team"
            elif event_type in ['PLAYER_UPDATE', 'PLAYER_INJURY', 'PLAYER_TRANSFER']:
                data["type"] = "player"
            else:
                data["type"] = "general"

        # Ensure deep-link route is always present
        if data and "match_id" in data and "route" not in data:
            data["route"] = f"/match/{data['match_id']}"
            data["deep_link"] = f"/match-detail?id={data['match_id']}"
        elif data and "league_id" in data and "route" not in data:
            data["route"] = f"/league/{data['league_id']}"
        elif data and "team_id" in data and "route" not in data and data.get("type") == "team":
            data["route"] = f"/team/{data['team_id']}"
        elif data and "player_id" in data and "route" not in data and data.get("type") == "player":
            data["route"] = f"/player/{data['player_id']}"
                
        # Ensure event_type, title, and body are in data for the mobile app
        if "event_type" not in data:
            data["event_type"] = event_type
        if "title" not in data and title:
            data["title"] = str(title)
        if "body" not in data and body:
            data["body"] = str(body)

        # Redis atomic deduplication guard for live sports events (Prevents worker concurrency races)
        import sys
        is_testing = 'test' in sys.argv
        dedup_eligible_events = [
            'GOAL', 'FULL_TIME', 'HALF_TIME', 'DISALLOWED_GOAL', 'LINEUPS', 'MATCH_START',
            'CARD', 'SUBSTITUTION', 'VAR', 'KICKOFF', 'SECOND_HALF', 'EXTRA_TIME',
            'PENALTY_SHOOTOUT', 'MISSED_PENALTY', 'OWN_GOAL', 'POSTPONED', 'SUSPENDED',
            'INTERRUPTED', 'ABANDONED', 'CANCELLED', 'WALKOVER'
        ]
        if not is_internal and not is_testing and event_type in dedup_eligible_events:
            try:
                from sports.tasks import get_redis_client
                r = get_redis_client()
                m_id = str(data.get("match_id", ""))
                sub_id = str(data.get("score") or data.get("card_type", "") + data.get("player_name", "") or data.get("player_in", "") or data.get("detail", "") or data.get("elapsed", "") or data.get("status", "") or "")
                dedup_key = f"notif_dedup:{event_type}:{topic}:{m_id}:{sub_id}"
                if not r.set(dedup_key, "1", nx=True, ex=7200):
                    print(f"🛑 [DEDUPLICATION GUARD] Suppressed duplicate push for {dedup_key}")
                    return False
            except Exception:
                pass
            
        # 1. Topic Fanout for base sports/global topics
        if not is_internal and (topic.startswith("team_") or topic.startswith("league_") or topic.startswith("match_") or topic.startswith("fixture_") or topic == "global"):
            # Create a single base notification log for user's personal inboxes
            try:
                NotificationLog.objects.create(
                    topic=topic,
                    title=title,
                    body=body,
                    status='SENT',
                    event_type=event_type,
                    error_message="Base sports topic logged for inbox. Fanned out to language topics.",
                    data=data
                )
            except Exception as e:
                print(f"Database logging failed for base topic {topic}: {e}")

            # Fan out to all supported languages concurrently to eliminate multi-second HTTP latency
            from concurrent.futures import ThreadPoolExecutor

            def _send_lang(lang):
                t_title, t_body = translate_notification(title, body, event_type, lang)
                lang_data = data.copy()
                lang_data["title"] = str(t_title)
                lang_data["body"] = str(t_body)
                return NotificationService.send_push_to_topic(
                    f"{topic}_{lang}", t_title, t_body, lang_data, event_type, is_internal=True
                )

            langs = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
            # Cap at 3 workers to prevent DB connection exhaustion during concurrent fan-outs
            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(_send_lang, langs))
            return True

        # 2. Translate user-specific notifications based on their profile language
        if not is_internal and topic.startswith("user_"):
            lang = 'en'
            try:
                user_id = int(topic.split('_')[1])
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.filter(id=user_id).first()
                if user:
                    if hasattr(user, 'fan_profile') and user.fan_profile.language:
                        lang = user.fan_profile.language
                    if not lang or lang == 'en':
                        from .models import UserDevice
                        dev = UserDevice.objects.filter(user=user, active=True).exclude(language__isnull=True).exclude(language='').first()
                        if dev and dev.language:
                            lang = dev.language
            except Exception as e:
                print(f"Error fetching language for user topic {topic}: {e}")

            if lang and lang != 'en':
                title, body = translate_notification(title, body, event_type, lang)
                data["title"] = str(title)
                data["body"] = str(body)

        # Check if we should use Simulated Dev Mode
        use_simulator = not getattr(settings, 'FIREBASE_CONFIGURED', False)
        
        if use_simulator:
            print(f"\n{'='*60}")
            print(f"⚠️  SIMULATOR MODE: FIREBASE CREDENTIALS MISSING OR PLACEHOLDER")
            print(f"   Simulating Push to Topic: {topic}")
            print(f"   Title: {title}")
            print(f"   Body: {body}")
            print(f"   Data: {data}")
            print(f"{'='*60}\n")
            
            # For user-specific or custom topics (not fanned out base topics), log to DB
            if topic.startswith("user_") or (not topic.startswith("team_") and not topic.startswith("league_") and not topic.startswith("match_") and not topic.startswith("fixture_") and topic != "global" and not is_internal):
                try:
                    NotificationLog.objects.create(
                        topic=topic,
                        title=title,
                        body=body,
                        status='SENT',
                        event_type=event_type,
                        error_message="[SIMULATED] User or custom topic.",
                        data=data
                    )
                except Exception as e:
                    print(f"Database logging failed: {e}")
            return True

        # Otherwise, proceed with actual Firebase sending
        NotificationService.ensure_firebase_initialized()
        
        status = 'SENT'
        error_msg = None
        
        # Determine deduplication collapse key / APNS collapse ID
        collapse_key = None
        if data and "match_id" in data:
            match_id = data["match_id"]
            if event_type == 'FULL_TIME':
                collapse_key = f"match_{match_id}_ft"
            elif event_type == 'HALF_TIME':
                collapse_key = f"match_{match_id}_ht"
            elif event_type in ['GOAL', 'DISALLOWED_GOAL']:
                collapse_key = f"match_{match_id}_goal"
            elif event_type == 'LINEUPS':
                collapse_key = f"match_{match_id}_lineups"
            elif event_type in ['MATCH_START', 'KICKOFF']:
                collapse_key = f"match_{match_id}_kickoff"
            elif event_type == 'SECOND_HALF':
                collapse_key = f"match_{match_id}_2h"
            elif event_type == 'EXTRA_TIME':
                collapse_key = f"match_{match_id}_et"
            elif event_type == 'PENALTY_SHOOTOUT':
                collapse_key = f"match_{match_id}_penalties"
            elif event_type in ['POSTPONED', 'SUSPENDED', 'INTERRUPTED', 'ABANDONED', 'CANCELLED', 'WALKOVER']:
                collapse_key = f"match_{match_id}_status"
            elif event_type == 'MISSED_PENALTY':
                elapsed = str(data.get("elapsed", ""))
                collapse_key = f"match_{match_id}_missedpen_{elapsed}"
            elif event_type == 'OWN_GOAL':
                elapsed = str(data.get("elapsed", ""))
                collapse_key = f"match_{match_id}_owngoal_{elapsed}"

        android_config = None
        apns_config = None

        # Build APNS configuration payload for iOS devices (sound, alert, badge, background delivery)
        apns_headers = {
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if collapse_key:
            apns_headers["apns-collapse-id"] = collapse_key

        apns_config = messaging.APNSConfig(
            headers=apns_headers,
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(
                        title=title,
                        body=body,
                    ),
                    sound="default",
                    badge=1,
                    content_available=True,
                    mutable_content=True,
                )
            )
        )

        android_kwargs = {
            "priority": "high",
            "notification": messaging.AndroidNotification(
                sound="default",
                priority="high",
                channel_id="high_importance_channel",
                default_sound=True,
                default_vibrate_timings=True,
            )
        }
        if collapse_key:
            android_kwargs["collapse_key"] = collapse_key

        android_config = messaging.AndroidConfig(**android_kwargs)

        try:
            # Firebase only accepts strings in data map
            formatted_data = {k: str(v) for k, v in data.items()}
            
            print(f"\n{'='*60}")
            print(f"🚀 PUSHING TO FIREBASE TOPIC: {topic}")
            print(f"   Title: {title}")
            print(f"   Body: {body}")
            print(f"   Data: {formatted_data}")
            print(f"   Collapse Key: {collapse_key}")
            print(f"{'='*60}\n")
            
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data=formatted_data,
                topic=topic,
                android=android_config,
                apns=apns_config,
            )
            response = messaging.send(message=message)
            error_msg = str(response) # Save Firebase Message ID to DB
            
            print(f"\n{'='*60}")
            print(f"✅ SUCCESS! FIREBASE ACCEPTED NOTIFICATION")
            print(f"   Message ID: {response}")
            print(f"   Topic: {topic}")
            print(f"{'='*60}\n")
        except Exception as e:
            status = "FAILED"
            error_msg = str(e)
            print(f"Push Error ({topic}): {e}")
            
            # --- USER DEMAND: PROOF OF JSON FILE ---
            try:
                with open(settings.FIREBASE_CREDENTIALS_PATH, 'r') as f:
                    print(f"\n{'='*60}")
                    print(f"📄 FIREBASE JSON FILE AS SEEN BY DOCKER (Proof):")
                    print(f.read())
                    print(f"{'='*60}\n")
            except Exception as read_err:
                print(f"Could not read the file for debugging: {read_err}")
        
        # Log to DB only for user topics or custom non-fanned-out topics to avoid duplicates
        if topic.startswith("user_") or (not topic.startswith("team_") and not topic.startswith("league_") and not topic.startswith("match_") and not topic.startswith("fixture_") and topic != "global" and not is_internal):
            try:
                NotificationLog.objects.create(
                    topic=topic,
                    title=title,
                    body=body,
                    status=status,
                    event_type=event_type,
                    error_message=error_msg,
                    data=data
                )
            except Exception:
                pass
        return status == 'SENT'
    
    @staticmethod
    def send_push_to_token(token, title, body, data=None):
        """
        TEMPORARY: Sends a message directly to a specific device FCM token for testing.
        If firebase credentials are missing, falls back to simulated sent state for dev/testing.
        """
        # Ensure data is dict and make a copy to avoid mutating the caller's dictionary
        data = data.copy() if data is not None else {}
        
        # Enforce click_action
        if "click_action" not in data:
            data["click_action"] = "FLUTTER_NOTIFICATION_CLICK"
            
        # Extract event_type from data if present, otherwise default to DEV_TEST
        evt_type = data.get("event_type") or data.get("type") or "DEV_TEST"
        
        # Enforce routing type if missing
        if "type" not in data:
            if evt_type in [
                'GOAL', 'FULL_TIME', 'LINEUPS', 'MATCH_START', 'CARD', 'SUBSTITUTION',
                'VAR', 'HALF_TIME', 'DISALLOWED_GOAL', 'KICKOFF', 'SECOND_HALF',
                'EXTRA_TIME', 'PENALTY_SHOOTOUT', 'MISSED_PENALTY', 'OWN_GOAL',
                'POSTPONED', 'SUSPENDED', 'INTERRUPTED', 'ABANDONED', 'CANCELLED',
                'WALKOVER', 'match'
            ]:
                data["type"] = "match"
            elif evt_type in ['SCHEDULE', 'LEAGUE_UPDATE', 'TABLE_UPDATE', 'TOP_SCORER', 'league']:
                data["type"] = "league"
            elif evt_type in ['TEAM_NEWS', 'team']:
                data["type"] = "team"
            elif evt_type in ['PLAYER_UPDATE', 'PLAYER_INJURY', 'PLAYER_TRANSFER', 'player']:
                data["type"] = "player"
            else:
                data["type"] = "general"

        # Ensure deep-link route is always present
        if data and "match_id" in data and "route" not in data:
            data["route"] = f"/match/{data['match_id']}"
            data["deep_link"] = f"/match-detail?id={data['match_id']}"
        elif data and "league_id" in data and "route" not in data:
            data["route"] = f"/league/{data['league_id']}"
        elif data and "team_id" in data and "route" not in data and data.get("type") == "team":
            data["route"] = f"/team/{data['team_id']}"
        elif data and "player_id" in data and "route" not in data and data.get("type") == "player":
            data["route"] = f"/player/{data['player_id']}"
                
        # Ensure event_type is present in data
        if "event_type" not in data:
            data["event_type"] = evt_type
        if "title" not in data:
            data["title"] = str(title)
        if "body" not in data:
            data["body"] = str(body)
        
        use_simulator = not getattr(settings, 'FIREBASE_CONFIGURED', False)
        
        # Determine inbox topic to associate the notification log with the user or guest inbox
        db_topic = f"token_{token[:20]}"
        try:
            from .models import UserDevice
            device = UserDevice.objects.filter(registration_id=token).first()
            if device:
                if device.user:
                    db_topic = f"user_{device.user.id}"
                elif device.guest_id:
                    db_topic = f"guest_{device.guest_id}"
        except Exception:
            pass

        if use_simulator:
            print(f"\n{'='*60}")
            print(f"⚠️  SIMULATOR MODE: FIREBASE CREDENTIALS MISSING OR PLACEHOLDER")
            print(f"   Simulating Push to Token: {token[:20]}...")
            print(f"   Title: {title}")
            print(f"   Body: {body}")
            print(f"   Data: {data}")
            print(f"{'='*60}\n")
            
            try:
                NotificationLog.objects.create(
                    topic=db_topic,
                    title=title,
                    body=body,
                    status='SENT',
                    event_type=evt_type,
                    error_message="[SIMULATED] Firebase credentials missing, simulated successfully.",
                    data=data
                )
            except Exception as e:
                print(f"Database logging failed: {e}")
                
            return {"success": True, "message_id": "simulated-msg-id-12345", "simulated": True}

        # Otherwise proceed with actual Firebase sending
        NotificationService.ensure_firebase_initialized()
        formatted_data = {k: str(v) for k, v in data.items()}
        
        # Determine deduplication collapse key / APNS collapse ID
        collapse_key = None
        if data and "match_id" in data:
            match_id = data["match_id"]
            if evt_type == 'FULL_TIME':
                collapse_key = f"match_{match_id}_ft"
            elif evt_type == 'HALF_TIME':
                collapse_key = f"match_{match_id}_ht"
            elif evt_type in ['GOAL', 'DISALLOWED_GOAL']:
                collapse_key = f"match_{match_id}_goal"
            elif evt_type == 'LINEUPS':
                collapse_key = f"match_{match_id}_lineups"
            elif evt_type in ['MATCH_START', 'KICKOFF']:
                collapse_key = f"match_{match_id}_kickoff"
            elif evt_type == 'SECOND_HALF':
                collapse_key = f"match_{match_id}_2h"
            elif evt_type == 'EXTRA_TIME':
                collapse_key = f"match_{match_id}_et"
            elif evt_type == 'PENALTY_SHOOTOUT':
                collapse_key = f"match_{match_id}_penalties"
            elif evt_type in ['POSTPONED', 'SUSPENDED', 'INTERRUPTED', 'ABANDONED', 'CANCELLED', 'WALKOVER']:
                collapse_key = f"match_{match_id}_status"
            elif evt_type == 'MISSED_PENALTY':
                elapsed = str(data.get("elapsed", ""))
                collapse_key = f"match_{match_id}_missedpen_{elapsed}"
            elif evt_type == 'OWN_GOAL':
                elapsed = str(data.get("elapsed", ""))
                collapse_key = f"match_{match_id}_owngoal_{elapsed}"

        android_config = None
        apns_config = None

        # Build APNS configuration payload for iOS devices (sound, alert, badge, background delivery)
        apns_headers = {
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if collapse_key:
            apns_headers["apns-collapse-id"] = collapse_key

        apns_config = messaging.APNSConfig(
            headers=apns_headers,
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(
                        title=title,
                        body=body,
                    ),
                    sound="default",
                    badge=1,
                    content_available=True,
                    mutable_content=True,
                )
            )
        )

        android_kwargs = {
            "priority": "high",
            "notification": messaging.AndroidNotification(
                sound="default",
                priority="high",
                channel_id="high_importance_channel",
                default_sound=True,
                default_vibrate_timings=True,
            )
        }
        if collapse_key:
            android_kwargs["collapse_key"] = collapse_key

        android_config = messaging.AndroidConfig(**android_kwargs)

        print(f"\n{'='*60}")
        print(f"🚀 PUSHING TO FIREBASE TOKEN: {token[:20]}...")
        print(f"   Title: {title}")
        print(f"   Body: {body}")
        print(f"   Collapse Key: {collapse_key}")
        print(f"{'='*60}\n")
        
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=formatted_data,
            token=token,
            android=android_config,
            apns=apns_config,
        )
        try:
            response = messaging.send(message=message)
            print(f"✅ SUCCESS! FIREBASE ACCEPTED TOKEN NOTIFICATION: {response}")
            
            try:
                NotificationLog.objects.create(
                    topic=db_topic,
                    title=title,
                    body=body,
                    status='SENT',
                    event_type=evt_type,
                    error_message=str(response),
                    data=data
                )
            except Exception:
                pass
                
            return {"success": True, "message_id": response}
        except Exception as e:
            print(f"❌ Failed to send to token: {e}")
            
            try:
                NotificationLog.objects.create(
                    topic=db_topic,
                    title=title,
                    body=body,
                    status='FAILED',
                    event_type=evt_type,
                    error_message=str(e),
                    data=data
                )
            except Exception:
                pass
                
            try:
                with open(settings.FIREBASE_CREDENTIALS_PATH, 'r') as f:
                    pass # Silenced to not clutter logs anymore
            except Exception:
                pass
            return {"success": False, "error": str(e)}

    @staticmethod
    def send_goal_alert(scoring_team_name, home_team_name, away_team_name, score, home_team_id, away_team_id, match_id, league_id=None):
        """
        Sends goal alerts to Team Topics, Match Topic, and optionally League Topic.
        """
        title = f"⚽ Goal by {scoring_team_name}!"
        body = f"Current Score: {home_team_name} {score} {away_team_name}"
                # 1. Send to Home Team Fans
        data_home = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "GOAL",
            "match_id": str(match_id), 
            "team_id": str(home_team_id),
            "score": str(score),
            "reason": f"Following {home_team_name}"
        }
        NotificationService.send_push_to_topic(f"team_{home_team_id}", title, body, data_home, event_type='GOAL')
        
        # 2. Send to Away Team Fans
        data_away = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "GOAL",
            "match_id": str(match_id), 
            "team_id": str(away_team_id),
            "score": str(score),
            "reason": f"Following {away_team_name}"
        }
        NotificationService.send_push_to_topic(f"team_{away_team_id}", title, body, data_away, event_type='GOAL')
        
        # 3. Send to Match Followers
        data_match = data_home.copy()
        data_match["reason"] = f"Saved Match"
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, event_type='GOAL')

        # 4. Send to League Followers (if provided)
        if league_id:
            data_league = data_home.copy()
            data_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, event_type='GOAL')

    @staticmethod
    def send_match_result_alert(home_team, away_team, score, match_id, league_id):
        """
        Sends Full Time results to BOTH teams, the Match Topic, AND the League Topic.
        """
        title = "🏁 Full Time"
        body = f"Final Result: {home_team.name} {score} {away_team.name}"
        
        # 1. Send to Home Team Fans
        data_home = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "FULL_TIME",
            "match_id": str(match_id),
            "reason": f"Following {home_team.name}"
        }
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, data_home, 'FULL_TIME')
        
        # 2. Send to Away Team Fans
        data_away = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "FULL_TIME",
            "match_id": str(match_id),
            "reason": f"Following {away_team.name}"
        }
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, data_away, 'FULL_TIME')
        
        # 3. Send to Match Followers
        data_match = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "FULL_TIME",
            "match_id": str(match_id),
            "reason": "Saved Match"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, 'FULL_TIME')

        # 4. Send to League Followers
        data_league = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "FULL_TIME",
            "match_id": str(match_id),
            "reason": "Following League"
        }
        NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, 'FULL_TIME')
    
    @staticmethod
    def send_lineup_alert(home_team, away_team, match_id, league_id=None):
        """
        Sends Lineup Confirmation to Team Topics, Match Topic, and League Topic.
        """
        title = "📋 Lineups Released"
        body = f"Starting XI is now available for {home_team.name} vs {away_team.name}"
        data_home = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "LINEUPS",
            "match_id": str(match_id),
            "reason": f"Following {home_team.name}"
        }
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, data_home, 'LINEUPS')

        data_away = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "LINEUPS",
            "match_id": str(match_id),
            "reason": f"Following {away_team.name}"
        }
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, data_away, 'LINEUPS')
        
        data_match = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "LINEUPS",
            "match_id": str(match_id),
            "reason": "Saved Match"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, 'LINEUPS')

        if league_id:
            data_league = {
                "click_action": "FLUTTER_NOTIFICATION_CLICK",
                "type": "match",
                "event_type": "LINEUPS",
                "match_id": str(match_id),
                "reason": "Following League"
            }
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, 'LINEUPS')
    
    @staticmethod
    def send_league_daily_update(league_name, match_count, league_id):
        """
        Sends a daily schedule summary for a league.
        """
        title = f"📅 {league_name} Schedule"
        body = f"There are {match_count} matches starting tomorrow in {league_name}. Don't miss out!"
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "league",
            "event_type": "SCHEDULE",
            "league_id": str(league_id),
            "reason": f"Following {league_name}"
        }
        NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data, 'SCHEDULE')

    @staticmethod
    def send_card_alert(player_name, card_type, team_name, team_id, match_id, elapsed_time, league_id=None):
        """
        Sends Card alerts (Yellow, Red, etc.) to Team Topics, Match Topic, and optionally League Topic.
        """
        card_emoji = "🟥" if "Red" in card_type or "Second" in card_type else "🟨"
        title = f"{card_emoji} Card for {player_name} ({team_name})"
        body = f"{player_name} received a {card_type} in the {elapsed_time}' minute."
        
        data_match = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "CARD",
            "match_id": str(match_id),
            "team_id": str(team_id),
            "player_name": player_name,
            "card_type": card_type,
            "elapsed": str(elapsed_time),
            "reason": "Saved Match"
        }
        
        # 1. Send to Match Followers
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, event_type='CARD')
        
        # 2. Send to Team Followers
        data_team = data_match.copy()
        data_team["reason"] = f"Following {team_name}"
        NotificationService.send_push_to_topic(f"team_{team_id}", title, body, data_team, event_type='CARD')
        
        # 3. Send to League Followers (if provided)
        if league_id:
            data_league = data_match.copy()
            data_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, event_type='CARD')

    @staticmethod
    def send_substitution_alert(player_in, player_out, team_name, team_id, match_id, elapsed_time, league_id=None):
        """
        Sends Substitution alerts to Team Topics, Match Topic, and optionally League Topic.
        """
        title = f"🔄 Substitution for {team_name}"
        body = f"In: {player_in} | Out: {player_out} ({elapsed_time}')"
        
        data_match = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "SUBSTITUTION",
            "match_id": str(match_id),
            "team_id": str(team_id),
            "player_in": player_in,
            "player_out": player_out,
            "elapsed": str(elapsed_time),
            "reason": "Saved Match"
        }
        
        # 1. Send to Match Followers
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, event_type='SUBSTITUTION')
        
        # 2. Send to Team Followers
        data_team = data_match.copy()
        data_team["reason"] = f"Following {team_name}"
        NotificationService.send_push_to_topic(f"team_{team_id}", title, body, data_team, event_type='SUBSTITUTION')
        
        # 3. Send to League Followers (if provided)
        if league_id:
            data_league = data_match.copy()
            data_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, event_type='SUBSTITUTION')

    @staticmethod
    def send_var_alert(detail, team_name, team_id, match_id, elapsed_time, league_id=None):
        """
        Sends VAR decision alerts to Team Topics, Match Topic, and optionally League Topic.
        """
        title = f"🖥️ VAR Decision: {team_name}"
        body = f"{detail} ({elapsed_time}')"
        
        data_match = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "VAR",
            "match_id": str(match_id),
            "team_id": str(team_id),
            "detail": detail,
            "elapsed": str(elapsed_time),
            "reason": "Saved Match"
        }
        
        # 1. Send to Match Followers
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, event_type='VAR')
        
        # 2. Send to Team Followers
        data_team = data_match.copy()
        data_team["reason"] = f"Following {team_name}"
        NotificationService.send_push_to_topic(f"team_{team_id}", title, body, data_team, event_type='VAR')
        
        # 3. Send to League Followers (if provided)
        if league_id:
            data_league = data_match.copy()
            data_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, event_type='VAR')

    @staticmethod
    def send_disallowed_goal_alert(team_name, home_team_name, away_team_name, score, home_team_id, away_team_id, match_id, league_id=None):
        """
        Sends Goal Disallowed alerts (VAR overturn / score deduction) to Team Topics, Match Topic, and League Topic.
        """
        title = f"❌ Goal Disallowed: {team_name}"
        body = f"VAR overturned the goal. Current Score: {home_team_name} {score} {away_team_name}"
        
        # 1. Send to Home Team Fans
        data_home = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "DISALLOWED_GOAL",
            "match_id": str(match_id),
            "team_id": str(home_team_id),
            "score": str(score),
            "reason": f"Following {home_team_name}"
        }
        NotificationService.send_push_to_topic(f"team_{home_team_id}", title, body, data_home, event_type='DISALLOWED_GOAL')
        
        # 2. Send to Away Team Fans
        data_away = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "DISALLOWED_GOAL",
            "match_id": str(match_id),
            "team_id": str(away_team_id),
            "score": str(score),
            "reason": f"Following {away_team_name}"
        }
        NotificationService.send_push_to_topic(f"team_{away_team_id}", title, body, data_away, event_type='DISALLOWED_GOAL')
        
        # 3. Send to Match Followers
        data_match = data_home.copy()
        data_match["reason"] = "Saved Match"
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, event_type='DISALLOWED_GOAL')
        
        # 4. Send to League Followers (if provided)
        if league_id:
            data_league = data_home.copy()
            data_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, event_type='DISALLOWED_GOAL')

    @staticmethod
    def send_half_time_alert(home_team, away_team, score, match_id, league_id=None):
        """
        Sends Half-Time score alerts to Team Topics, Match Topic, and League Topic.
        """
        title = "⏸️ Half Time"
        body = f"Half-Time Score: {home_team.name} {score} {away_team.name}"
        
        # 1. Send to Home Team Fans
        data_home = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "HALF_TIME",
            "match_id": str(match_id),
            "team_id": str(home_team.id),
            "score": str(score),
            "reason": f"Following {home_team.name}"
        }
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, data_home, event_type='HALF_TIME')
        
        # 2. Send to Away Team Fans
        data_away = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "HALF_TIME",
            "match_id": str(match_id),
            "team_id": str(away_team.id),
            "score": str(score),
            "reason": f"Following {away_team.name}"
        }
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, data_away, event_type='HALF_TIME')
        
        # 3. Send to Match Followers
        data_match = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "HALF_TIME",
            "match_id": str(match_id),
            "score": str(score),
            "reason": "Saved Match"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data_match, event_type='HALF_TIME')
        
        # 4. Send to League Followers
        if league_id:
            data_league = {
                "click_action": "FLUTTER_NOTIFICATION_CLICK",
                "type": "match",
                "event_type": "HALF_TIME",
                "match_id": str(match_id),
                "score": str(score),
                "reason": "Following League"
            }
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, data_league, event_type='HALF_TIME')

    @staticmethod
    def send_kickoff_alert(home_team, away_team, match_id, league_id=None):
        """
        Sends Kick-off (1H start) alerts to Team Topics, Match Topic, and League Topic.
        """
        title = f"⏱️ Kick-off: {home_team.name} vs {away_team.name}"
        body = "The match has officially started!"
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "KICKOFF",
            "match_id": str(match_id),
            "route": f"/match/{match_id}",
            "reason": "Match Kick-off"
        }
        # 1. Match followers
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type='KICKOFF')
        # 2. Team followers
        d_home = data.copy()
        d_home["reason"] = f"Following {home_team.name}"
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, d_home, event_type='KICKOFF')
        d_away = data.copy()
        d_away["reason"] = f"Following {away_team.name}"
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, d_away, event_type='KICKOFF')
        # 3. League followers
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type='KICKOFF')

    @staticmethod
    def send_second_half_alert(home_team, away_team, score, match_id, league_id=None):
        """
        Sends 2nd Half Kick-off alerts to Team Topics, Match Topic, and League Topic.
        """
        title = "▶️ Second Half Underway"
        body = f"Second half has begun: {home_team.name} {score} {away_team.name}"
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "SECOND_HALF",
            "match_id": str(match_id),
            "score": str(score),
            "route": f"/match/{match_id}",
            "reason": "Second Half Kick-off"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type='SECOND_HALF')
        d_home = data.copy()
        d_home["reason"] = f"Following {home_team.name}"
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, d_home, event_type='SECOND_HALF')
        d_away = data.copy()
        d_away["reason"] = f"Following {away_team.name}"
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, d_away, event_type='SECOND_HALF')
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type='SECOND_HALF')

    @staticmethod
    def send_extra_time_alert(home_team, away_team, score, match_id, league_id=None):
        """
        Sends Extra Time start alerts to Team Topics, Match Topic, and League Topic.
        """
        title = "⏳ Extra Time Started"
        body = f"Extra time has begun: {home_team.name} {score} {away_team.name}"
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "EXTRA_TIME",
            "match_id": str(match_id),
            "score": str(score),
            "route": f"/match/{match_id}",
            "reason": "Extra Time"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type='EXTRA_TIME')
        d_home = data.copy()
        d_home["reason"] = f"Following {home_team.name}"
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, d_home, event_type='EXTRA_TIME')
        d_away = data.copy()
        d_away["reason"] = f"Following {away_team.name}"
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, d_away, event_type='EXTRA_TIME')
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type='EXTRA_TIME')

    @staticmethod
    def send_penalty_shootout_alert(home_team, away_team, match_id, league_id=None):
        """
        Sends Penalty Shootout start alerts to Team Topics, Match Topic, and League Topic.
        """
        title = "🎯 Penalty Shootout"
        body = f"Penalty shootout is underway: {home_team.name} vs {away_team.name}!"
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "PENALTY_SHOOTOUT",
            "match_id": str(match_id),
            "route": f"/match/{match_id}",
            "reason": "Penalty Shootout"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type='PENALTY_SHOOTOUT')
        d_home = data.copy()
        d_home["reason"] = f"Following {home_team.name}"
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, d_home, event_type='PENALTY_SHOOTOUT')
        d_away = data.copy()
        d_away["reason"] = f"Following {away_team.name}"
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, d_away, event_type='PENALTY_SHOOTOUT')
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type='PENALTY_SHOOTOUT')

    @staticmethod
    def send_missed_penalty_alert(player_name, team_name, team_id, match_id, elapsed_time, league_id=None):
        """
        Sends Missed Penalty alerts to Team Topics, Match Topic, and League Topic.
        """
        title = f"❌ Penalty Missed by {player_name} ({team_name})"
        body = f"Penalty was missed/saved in the {elapsed_time}' minute."
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "MISSED_PENALTY",
            "match_id": str(match_id),
            "team_id": str(team_id),
            "player_name": str(player_name),
            "elapsed": str(elapsed_time),
            "route": f"/match/{match_id}",
            "reason": "Saved Match"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type='MISSED_PENALTY')
        d_team = data.copy()
        d_team["reason"] = f"Following {team_name}"
        NotificationService.send_push_to_topic(f"team_{team_id}", title, body, d_team, event_type='MISSED_PENALTY')
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type='MISSED_PENALTY')

    @staticmethod
    def send_own_goal_alert(player_name, team_name, team_id, home_team_name, away_team_name, score, match_id, elapsed_time, league_id=None):
        """
        Sends Own Goal alerts to Team Topics, Match Topic, and League Topic.
        """
        title = f"🤦 Own Goal by {player_name} ({team_name})!"
        body = f"Current Score: {home_team_name} {score} {away_team_name} ({elapsed_time}')"
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": "OWN_GOAL",
            "match_id": str(match_id),
            "team_id": str(team_id),
            "player_name": str(player_name),
            "score": str(score),
            "elapsed": str(elapsed_time),
            "route": f"/match/{match_id}",
            "reason": "Saved Match"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type='OWN_GOAL')
        d_team = data.copy()
        d_team["reason"] = f"Following {team_name}"
        NotificationService.send_push_to_topic(f"team_{team_id}", title, body, d_team, event_type='OWN_GOAL')
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type='OWN_GOAL')

    @staticmethod
    def send_match_disruption_alert(home_team, away_team, status_short, match_id, league_id=None):
        """
        Sends Match Disruption / Postponement / Suspension / Abandonment alerts.
        """
        status_map = {
            'PST': ('POSTPONED', '⚠️ Match Postponed', f"{home_team.name} vs {away_team.name} has been postponed."),
            'SUSP': ('SUSPENDED', '⚠️ Match Suspended', f"{home_team.name} vs {away_team.name} has been temporarily suspended."),
            'INT': ('INTERRUPTED', '⏸️ Match Interrupted', f"{home_team.name} vs {away_team.name} has been interrupted."),
            'ABD': ('ABANDONED', '🛑 Match Abandoned', f"{home_team.name} vs {away_team.name} has been abandoned."),
            'CANC': ('CANCELLED', '🚫 Match Cancelled', f"{home_team.name} vs {away_team.name} has been cancelled."),
            'AWD': ('WALKOVER', '⚖️ Match Awarded / Walkover', f"Match awarded for {home_team.name} vs {away_team.name}."),
            'WO': ('WALKOVER', '⚖️ Match Awarded / Walkover', f"Match awarded for {home_team.name} vs {away_team.name}.")
        }
        info = status_map.get(status_short, ('POSTPONED', '⚠️ Match Disruption', f"Status update for {home_team.name} vs {away_team.name}."))
        event_type, title_prefix, default_body = info
        title = f"{title_prefix}: {home_team.name} vs {away_team.name}"
        body = default_body
        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "match",
            "event_type": event_type,
            "match_id": str(match_id),
            "status": str(status_short),
            "route": f"/match/{match_id}",
            "reason": "Match Status Update"
        }
        NotificationService.send_push_to_topic(f"match_{match_id}", title, body, data, event_type=event_type)
        d_home = data.copy()
        d_home["reason"] = f"Following {home_team.name}"
        NotificationService.send_push_to_topic(f"team_{home_team.id}", title, body, d_home, event_type=event_type)
        d_away = data.copy()
        d_away["reason"] = f"Following {away_team.name}"
        NotificationService.send_push_to_topic(f"team_{away_team.id}", title, body, d_away, event_type=event_type)
        if league_id:
            d_league = data.copy()
            d_league["reason"] = "Following League"
            NotificationService.send_push_to_topic(f"league_{league_id}", title, body, d_league, event_type=event_type)


    # --- Subscription Helpers ---
    @staticmethod
    def subscribe_tokens_to_topic(tokens, topic):
        NotificationService.ensure_firebase_initialized()
        if not tokens: return
        
        supported_langs = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
        has_lang_suffix = any(topic.endswith(f"_{l}") for l in supported_langs)
        
        if has_lang_suffix:
            batch_size = 1000
            for i in range(0, len(tokens), batch_size):
                batch = tokens[i:i + batch_size]
                try:
                    messaging.subscribe_to_topic(batch, topic)
                    print(f"Subscribed {len(batch)} tokens to {topic}")
                except Exception as e:
                    print(f"Error subscribing to {topic}: {e}")
            return
        
        # Determine language for each token
        from .models import UserDevice
        devices = UserDevice.objects.filter(registration_id__in=tokens)
        token_to_lang = {d.registration_id: d.language for d in devices}
        
        # Group tokens by language
        lang_groups = {}
        for token in tokens:
            lang = token_to_lang.get(token, 'en')
            lang_groups.setdefault(lang, []).append(token)
            
        for lang, lang_tokens in lang_groups.items():
            lang_topic = f"{topic}_{lang}"
            batch_size = 1000
            for i in range(0, len(lang_tokens), batch_size):
                batch = lang_tokens[i:i + batch_size]
                try:
                    messaging.subscribe_to_topic(batch, lang_topic)
                    print(f"Subscribed {len(batch)} tokens to {lang_topic}")
                except Exception as e:
                    print(f"Error subscribing to {lang_topic}: {e}")

    @staticmethod
    def unsubscribe_tokens_from_topic(tokens, topic):
        NotificationService.ensure_firebase_initialized()
        if not tokens: return
        
        batch_size = 1000
        for i in range(0, len(tokens), batch_size):
            batch = tokens[i:i + batch_size]
            try:
                messaging.unsubscribe_from_topic(batch, topic)
                print(f"Unsubscribed {len(batch)} tokens from {topic}")
            except Exception as e:
                print(f"Error unsubscribing from {topic}: {e}")

def unsubscribe_deactivated_device(device):
    """
    Unsubscribes a deactivated device (active=False) from all FCM topics.
    """
    if not device or not device.registration_id:
        return
    languages = ['en', 'es', 'fr', 'de', 'it', 'pt', 'tr']
    all_topics = ['global'] + [f'global_{l}' for l in languages]
    
    team_ids, league_ids, fixture_ids = [], [], []
    if device.user and hasattr(device.user, 'fan_profile'):
        profile = device.user.fan_profile
        team_ids = list(profile.favorite_teams.values_list('id', flat=True))
        league_ids = list(profile.favorite_leagues.values_list('id', flat=True))
        fixture_ids = list(profile.favorite_fixtures.values_list('id', flat=True))
    elif device.guest_id:
        from users.models import GuestFavorite
        fav = GuestFavorite.objects.filter(device_id=device.guest_id).first()
        if fav:
            team_ids = list(fav.favorite_teams.values_list('id', flat=True))
            league_ids = list(fav.favorite_leagues.values_list('id', flat=True))
            fixture_ids = list(fav.favorite_fixtures.values_list('id', flat=True))
            
    for tid in team_ids:
        all_topics.append(f'team_{tid}')
        for l in languages:
            all_topics.append(f'team_{tid}_{l}')
    for lid in league_ids:
        all_topics.append(f'league_{lid}')
        for l in languages:
            all_topics.append(f'league_{lid}_{l}')
    for fid in fixture_ids:
        all_topics.extend([f'match_{fid}', f'fixture_{fid}'])
        for l in languages:
            all_topics.extend([f'match_{fid}_{l}', f'fixture_{fid}_{l}'])
            
    for topic in all_topics:
        try:
            NotificationService.unsubscribe_tokens_from_topic([device.registration_id], topic)
        except Exception:
            pass
