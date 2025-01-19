C HELLO WORLD PROGRAM

DECIMA    FIODEC
          EXTERNAL .IO1,.IO2,.IO3,.IO4,.IO5,.IO6,.IO7,.IO8,.IO9
          EXTERNAL .IO57A,.IODEC

          CALST
          EXTERNAL EARITH
          JMS EARITH
          WRITE
          JMS .IO2
          2
          FOR .10
          ENDIO

          JMP .AAA
.10,      106508
          TEXT
 HELLO, WORL
          0
          -65535

.AAA,     LFM
          HLT
TEM,

TEM+0/

START
