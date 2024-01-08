#!/bin/sh

a=2

while [ $a -lt 11 ]
do
   kubectl scale --replicas=$a deployment/webbooks
   a=`expr $a + 1`
   sleep 2
done
