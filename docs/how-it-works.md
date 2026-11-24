# How Home Air works: the forecast and corrected PM2.5

The actions and their limits are in the [README](../README.md#how-it-works). Below are two details the actions depend on: the PM2.5 forecast and the corrected PM2.5 values.

## The PM2.5 forecast

The server asks [Open-Meteo's Air Quality API](https://open-meteo.com/en/docs/air-quality-api) (free, no key) for hourly PM2.5 at the home, the past 24 hours and the next 24, and keeps the answer 30 minutes. For each past hour it compares your outdoor monitor's average with the forecast and takes the median ratio, kept between ×0.1 and ×3, so one unusual hour doesn't change it much; the coming hours are multiplied by it. With fewer than 6 matching hours it starts the forecast from the monitor's reading now instead, and the gap fades over 6 hours. Without an outdoor monitor, the home's chosen source stands in for it: the nearby public monitor's readings, or for the area estimate the hourly median of up to 5 public outdoor monitors within 15 km (at least 2). With the model alone, the forecast is used as Open-Meteo gives it. The actions look 8 hours ahead on that curve for when PM2.5 crosses the limit.

## Corrected PM2.5

AirGradient monitors measure particles with a laser sensor (Plantower). Outdoors it reads high, more so in humid air, where particles swell with water. The US EPA published a correction for this based on humidity ([Barkjohn et al. 2021](https://amt.copernicus.org/articles/14/4617/2021/)); indoors, AirGradient may correct its sensor differently ([AirGradient on low readings](https://www.airgradient.com/blog/low-readings-from-pms5003/)).

The app always uses the corrected value: for your monitors, the `pm02_corrected` value [AirGradient's API](https://api.airgradient.com/public/docs/api/v1/) sends; for public outdoor monitors, the EPA-corrected value from AirGradient's map ([AirGradient on the correction](https://meetingorganizer.copernicus.org/EGU25/EGU25-8701.html)), or the same formula when only the raw value is there. The rules compare these corrected values with the limits.
